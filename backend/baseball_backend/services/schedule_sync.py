"""Sync MLB schedule data into Postgres."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from baseball_analyze.data.mlb_client import (
    ScheduledGame,
    fetch_schedule_by_game_pk,
    fetch_schedule_for_date,
    fetch_schedule_season,
    fetch_teams,
)
from baseball_backend.db.models import Game, Player, Team

logger = logging.getLogger(__name__)

_FINAL_STATES = frozenset({"Final", "Game Over", "Completed Early"})


def _team_name_and_city(team_payload: dict[str, Any], abbrev: str) -> tuple[str, str | None]:
    """
    Return (nickname, geo) for display as ``f"{geo} {name}"``.

    Prefer the branded geography from the official club ``name`` (e.g. Arizona,
    Texas, Tampa Bay, New York) rather than MLB ``locationName``, which is often
    the venue city (Phoenix, Arlington, St. Petersburg, Bronx).
    """
    club = team_payload.get("clubName") or team_payload.get("teamName")
    full_name = team_payload.get("name")
    nickname = str(club).strip() if club else None

    geo: str | None = None
    if full_name and nickname:
        full = str(full_name).strip()
        # ``Arizona Diamondbacks`` / ``Texas Rangers`` / ``New York Yankees``
        if full.lower().endswith(f" {nickname.lower()}"):
            geo = full[: -(len(nickname) + 1)].strip() or None
        elif full.lower() != nickname.lower():
            # Official name without a separable nickname suffix — keep as-is geo None
            # and fall back below.
            pass

    if geo is None:
        city_raw = team_payload.get("locationName") or team_payload.get("franchiseName")
        geo = str(city_raw).strip() if city_raw else None

    if not nickname:
        if full_name:
            nickname = str(full_name).strip()
            if geo and nickname.lower().startswith(f"{geo.lower()} "):
                nickname = nickname[len(geo) :].strip()
        else:
            nickname = abbrev

    # Athletics-style: official name equals nickname → no geo prefix.
    if geo and nickname and geo.lower() == nickname.lower():
        geo = None

    return nickname or abbrev, geo


def _upsert_team(
    db: Session,
    team_id: int,
    abbrev: str,
    teams_cache: dict[int, dict[str, Any]],
    *,
    local_teams: dict[int, Team] | None = None,
) -> Team:
    if local_teams is not None and team_id in local_teams:
        team = local_teams[team_id]
    else:
        team = db.get(Team, team_id)
    payload = teams_cache.get(team_id, {})
    name, city = _team_name_and_city(payload, abbrev)
    if team is None:
        team = Team(id=team_id, abbreviation=abbrev, name=name, city=city)
        db.add(team)
    else:
        team.abbreviation = abbrev
        team.name = name
        team.city = city
    if local_teams is not None:
        local_teams[team_id] = team
    return team


def _upsert_player(
    db: Session,
    player_id: int,
    full_name: str,
    team_id: int | None,
    *,
    local_players: dict[int, Player] | None = None,
) -> Player:
    if local_players is not None and player_id in local_players:
        player = local_players[player_id]
    else:
        player = db.get(Player, player_id)
    if player is None:
        player = Player(id=player_id, full_name=full_name, team_id=team_id)
        db.add(player)
    else:
        player.full_name = full_name
        player.team_id = team_id
    if local_players is not None:
        local_players[player_id] = player
    return player


def _outcome_fields(scheduled: ScheduledGame) -> dict[str, int | str | None]:
    """Derive scores and winner from schedule payload when available."""
    fields: dict[str, int | str | None] = {}
    if scheduled.home_score is not None:
        fields["home_score"] = scheduled.home_score
    if scheduled.away_score is not None:
        fields["away_score"] = scheduled.away_score

    if scheduled.detailed_state in _FINAL_STATES:
        home_score = scheduled.home_score
        away_score = scheduled.away_score
        if home_score is not None and away_score is not None and home_score != away_score:
            fields["winner"] = (
                scheduled.home_abbrev
                if home_score > away_score
                else scheduled.away_abbrev
            )
        else:
            fields["winner"] = None
    return fields


def _upsert_game(
    db: Session,
    scheduled: ScheduledGame,
    *,
    local_games: dict[int, Game] | None = None,
) -> tuple[Game, str | None, str | None]:
    game_pk = int(scheduled.game_pk)
    if local_games is not None and game_pk in local_games:
        game = local_games[game_pk]
    else:
        game = db.scalar(select(Game).where(Game.game_pk == game_pk))
    previous_status = game.status if game is not None else None
    previous_detailed = game.detailed_state if game is not None else None
    fields = {
        "game_date": date.fromisoformat(scheduled.game_date),
        "season": scheduled.season,
        "status": scheduled.status,
        "detailed_state": scheduled.detailed_state,
        "home_team_id": scheduled.home_team_id,
        "away_team_id": scheduled.away_team_id,
        "venue_id": scheduled.venue_id,
        "venue_name": scheduled.venue_name,
        "home_probable_pitcher_id": scheduled.home_probable_id,
        "away_probable_pitcher_id": scheduled.away_probable_id,
        **_outcome_fields(scheduled),
    }
    if game is None:
        game = Game(game_pk=game_pk, **fields)
        db.add(game)
    else:
        for key, value in fields.items():
            setattr(game, key, value)
    if local_games is not None:
        local_games[game_pk] = game
    return game, previous_status, previous_detailed


def _dedupe_scheduled_games(scheduled_games: Iterable[ScheduledGame]) -> list[ScheduledGame]:
    """Keep the last row per ``game_pk`` (MLB season payloads can repeat games)."""
    by_pk: dict[int, ScheduledGame] = {}
    for scheduled in scheduled_games:
        by_pk[int(scheduled.game_pk)] = scheduled
    return list(by_pk.values())


def _prefetch_games_by_pk(db: Session, game_pks: Iterable[int]) -> dict[int, Game]:
    """Load existing games for ``game_pks`` in chunks (avoids N+1 selects)."""
    unique_pks = list({int(pk) for pk in game_pks})
    found: dict[int, Game] = {}
    chunk_size = 500
    for i in range(0, len(unique_pks), chunk_size):
        chunk = unique_pks[i : i + chunk_size]
        if not chunk:
            continue
        for game in db.scalars(select(Game).where(Game.game_pk.in_(chunk))).all():
            found[int(game.game_pk)] = game
    return found


def _sync_scheduled_games(
    db: Session,
    scheduled_games: Iterable[ScheduledGame],
    *,
    evaluate_alerts: bool = True,
    teams_cache: dict[int, dict[str, Any]] | None = None,
) -> int:
    """
    Upsert teams/players/games from an iterable of ``ScheduledGame`` rows.

    Returns the number of games processed. When ``evaluate_alerts`` is True,
    evaluates start/final alert rules after commit (idempotent via AlertDispatch).
    Bulk backfills should pass ``evaluate_alerts=False`` to avoid flooding followers.
    """
    if teams_cache is None:
        teams_cache = {int(team["id"]): team for team in fetch_teams()}

    games = _dedupe_scheduled_games(scheduled_games)
    status_transitions: list[tuple[Game, str | None, str | None]] = []
    local_teams: dict[int, Team] = {}
    local_players: dict[int, Player] = {}
    # Session uses autoflush=False: pending inserts are invisible to later SELECTs,
    # so track games by game_pk in-memory and prefetch rows already in Postgres.
    local_games = _prefetch_games_by_pk(db, (g.game_pk for g in games))

    for scheduled in games:
        _upsert_team(
            db,
            scheduled.home_team_id,
            scheduled.home_abbrev,
            teams_cache,
            local_teams=local_teams,
        )
        _upsert_team(
            db,
            scheduled.away_team_id,
            scheduled.away_abbrev,
            teams_cache,
            local_teams=local_teams,
        )

        if scheduled.home_probable_id and scheduled.home_probable_name:
            _upsert_player(
                db,
                scheduled.home_probable_id,
                scheduled.home_probable_name,
                scheduled.home_team_id,
                local_players=local_players,
            )
        if scheduled.away_probable_id and scheduled.away_probable_name:
            _upsert_player(
                db,
                scheduled.away_probable_id,
                scheduled.away_probable_name,
                scheduled.away_team_id,
                local_players=local_players,
            )

        game, prev_status, prev_detailed = _upsert_game(
            db, scheduled, local_games=local_games
        )
        status_transitions.append((game, prev_status, prev_detailed))

    db.commit()

    if evaluate_alerts:
        try:
            from baseball_backend.services.alert_rules import evaluate_schedule_status_alerts

            for game, prev_status, prev_detailed in status_transitions:
                evaluate_schedule_status_alerts(
                    db,
                    game,
                    previous_status=prev_status,
                    previous_detailed_state=prev_detailed,
                )
        except Exception:
            logger.exception("Schedule alert evaluation failed during sync")

    return len(games)


def _reconcile_misdated_games_for_date(
    db: Session,
    game_date: str,
    scheduled_games: list[ScheduledGame],
) -> list[ScheduledGame]:
    """
    Re-fetch DB games stored on ``game_date`` that MLB did not return for that slate.

    Late West Coast / ET games used to be stored on the UTC day of ``gameDate``
    instead of MLB ``officialDate``. Syncing only the current day never touched
    those rows; re-fetching by ``game_pk`` corrects ``game_date`` on upsert.
    """
    slate_pks = {int(g.game_pk) for g in scheduled_games}
    target = date.fromisoformat(game_date)
    query = select(Game).where(Game.game_date == target)
    if slate_pks:
        query = query.where(Game.game_pk.notin_(slate_pks))
    orphans = list(db.scalars(query).all())
    if not orphans:
        return []

    corrected: list[ScheduledGame] = []
    for game in orphans:
        try:
            refreshed = fetch_schedule_by_game_pk(int(game.game_pk))
        except Exception:
            logger.exception(
                "Failed to reconcile misdated game",
                extra={"game_pk": game.game_pk, "game_date": game_date},
            )
            continue
        if refreshed is None:
            logger.warning(
                "No MLB schedule row while reconciling misdated game",
                extra={"game_pk": game.game_pk, "game_date": game_date},
            )
            continue
        if refreshed.game_date != game_date:
            logger.info(
                "Correcting misdated game_date",
                extra={
                    "game_pk": game.game_pk,
                    "from_date": game_date,
                    "to_date": refreshed.game_date,
                },
            )
            corrected.append(refreshed)
    return corrected


def sync_schedule_for_date(
    db: Session,
    game_date: str,
    *,
    evaluate_alerts: bool = True,
) -> int:
    """
    Fetch MLB schedule for ``game_date`` (YYYY-MM-DD) and upsert teams/games.

    Also re-fetches any DB games currently stored on that date that are missing
    from MLB's slate (common after UTC ``gameDate`` mis-binning) so their
    ``game_date`` can move back to ``officialDate``.

    Returns the number of games synced (slate + reconciled).
    """
    scheduled_games = list(fetch_schedule_for_date(game_date))
    scheduled_games.extend(
        _reconcile_misdated_games_for_date(db, game_date, scheduled_games)
    )
    return _sync_scheduled_games(
        db,
        scheduled_games,
        evaluate_alerts=evaluate_alerts,
    )


def sync_schedule_for_season(
    db: Session,
    season: int,
    *,
    evaluate_alerts: bool = False,
    game_type: str = "R",
) -> int:
    """
    Fetch a full MLB season schedule in one API call and upsert into Postgres.

    Defaults to regular season (``game_type="R"``). Alert evaluation is off by
    default so historical backfills do not notify followers.
    """
    scheduled_games = list(
        fetch_schedule_season(season, game_type=game_type, hydrate_probable=True)
    )
    return _sync_scheduled_games(
        db,
        scheduled_games,
        evaluate_alerts=evaluate_alerts,
    )


def sync_schedule_for_date_range(
    db: Session,
    start: date | str,
    end: date | str,
    *,
    evaluate_alerts: bool = False,
    game_type: str = "R",
) -> int:
    """
    Sync all regular-season games with ``start <= game_date <= end``.

    Fetches each overlapping season once via ``fetch_schedule_season``, then
    filters by calendar date (much faster than one request per day).
    """
    start_date = date.fromisoformat(start) if isinstance(start, str) else start
    end_date = date.fromisoformat(end) if isinstance(end, str) else end
    if end_date < start_date:
        raise ValueError(f"end date {end_date} is before start date {start_date}")

    teams_cache = {int(team["id"]): team for team in fetch_teams()}
    matched: list[ScheduledGame] = []
    for season in range(start_date.year, end_date.year + 1):
        for scheduled in fetch_schedule_season(
            season, game_type=game_type, hydrate_probable=True
        ):
            try:
                game_day = date.fromisoformat(scheduled.game_date)
            except ValueError:
                continue
            if start_date <= game_day <= end_date:
                matched.append(scheduled)

    return _sync_scheduled_games(
        db,
        matched,
        evaluate_alerts=evaluate_alerts,
        teams_cache=teams_cache,
    )
