"""Team, player, and matchup analytics from stored games/events and FanGraphs caches."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from baseball_analyze.data.team_mapping import mlb_abbrev_to_fangraphs
from baseball_backend.db.models import Game, GameEvent, Player, Prediction, Team
from baseball_backend.services.model_registry import get_active_pregame_model

logger = logging.getLogger(__name__)

_FINAL_STATES = frozenset({"Final", "Game Over", "Completed Early"})


class TeamNotFoundError(LookupError):
    pass


class PlayerNotFoundError(LookupError):
    pass


def _is_scored_game(game: Game) -> bool:
    if game.detailed_state not in _FINAL_STATES:
        return False
    if game.home_score is None or game.away_score is None:
        return False
    return game.home_score != game.away_score


def _team_won(game: Game, team_id: int) -> bool:
    assert game.home_score is not None and game.away_score is not None
    if game.home_team_id == team_id:
        return game.home_score > game.away_score
    return game.away_score > game.home_score


def _runs_for_against(game: Game, team_id: int) -> tuple[int, int]:
    assert game.home_score is not None and game.away_score is not None
    if game.home_team_id == team_id:
        return int(game.home_score), int(game.away_score)
    return int(game.away_score), int(game.home_score)


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    return result


def _fangraphs_team_stats(abbreviation: str, season: int) -> dict[str, float | None] | None:
    """Load season batting/pitching row from FanGraphs disk cache when available."""
    try:
        from baseball_analyze.data.fangraphs_features import (
            bullpen_fip_by_team,
            load_team_batting,
            load_team_pitching,
            median_starter_fip_by_team,
        )
    except Exception as exc:  # pragma: no cover - import guard
        logger.debug("FanGraphs helpers unavailable: %s", exc)
        return None

    fg_team = mlb_abbrev_to_fangraphs(abbreviation, season)
    try:
        bat = load_team_batting(season)
        pit = load_team_pitching(season)
        bull = bullpen_fip_by_team(season)
        med_st = median_starter_fip_by_team(season)
    except Exception as exc:
        logger.info("FanGraphs team stats unavailable for %s/%s: %s", fg_team, season, exc)
        return None

    wrc = None
    team_fip = None
    if fg_team in bat.index:
        wrc = _safe_float(bat.loc[fg_team, "wRC+"] if "wRC+" in bat.columns else None)
    if fg_team in pit.index:
        team_fip = _safe_float(pit.loc[fg_team, "FIP"] if "FIP" in pit.columns else None)

    bullpen = _safe_float(bull.get(fg_team)) if hasattr(bull, "get") else None
    if bullpen is None and fg_team in getattr(bull, "index", []):
        bullpen = _safe_float(bull.loc[fg_team])

    starter = _safe_float(med_st.get(fg_team)) if hasattr(med_st, "get") else None
    if starter is None and fg_team in getattr(med_st, "index", []):
        starter = _safe_float(med_st.loc[fg_team])

    if all(v is None for v in (wrc, team_fip, bullpen, starter)):
        return None

    return {
        "fangraphs_team": fg_team,
        "wrc_plus": wrc,
        "team_fip": team_fip,
        "bullpen_fip": bullpen,
        "median_starter_fip": starter,
    }


def _fangraphs_pitcher_stats(
    full_name: str,
    team_abbreviation: str | None,
    season: int,
) -> dict[str, float | None | str] | None:
    """Best-effort pitcher season row matched by name (and team when possible)."""
    try:
        from baseball_analyze.data.fangraphs_features import load_pitcher_season_stats
    except Exception as exc:  # pragma: no cover
        logger.debug("FanGraphs pitcher helpers unavailable: %s", exc)
        return None

    try:
        df = load_pitcher_season_stats(season)
    except Exception as exc:
        logger.info("FanGraphs pitcher stats unavailable for %s: %s", season, exc)
        return None

    if df.empty or "Name" not in df.columns:
        return None

    name_key = full_name.strip().lower()
    matches = df[df["Name"].astype(str).str.strip().str.lower() == name_key]
    if matches.empty:
        # Allow trailing Jr./Sr. mismatches by prefix.
        matches = df[df["Name"].astype(str).str.strip().str.lower().str.startswith(name_key)]
    if matches.empty:
        return None

    if team_abbreviation and "Team" in matches.columns:
        fg_team = mlb_abbrev_to_fangraphs(team_abbreviation, season)
        team_matches = matches[matches["Team"].astype(str) == fg_team]
        if not team_matches.empty:
            matches = team_matches

    row = matches.iloc[0]
    return {
        "fangraphs_name": str(row.get("Name")),
        "fangraphs_team": str(row.get("Team")) if row.get("Team") is not None else None,
        "fip": _safe_float(row.get("FIP")),
        "era": _safe_float(row.get("ERA")),
        "ip": _safe_float(row.get("IP")),
        "gs": _safe_float(row.get("GS")),
        "k_per_9": _safe_float(row.get("K/9")),
        "bb_per_9": _safe_float(row.get("BB/9")),
    }


def _load_team_games(db: Session, team_id: int, season: int) -> list[Game]:
    return list(
        db.scalars(
            select(Game)
            .options(
                joinedload(Game.home_team),
                joinedload(Game.away_team),
                joinedload(Game.home_probable_pitcher),
                joinedload(Game.away_probable_pitcher),
            )
            .where(
                Game.season == season,
                or_(Game.home_team_id == team_id, Game.away_team_id == team_id),
            )
            .order_by(Game.game_date.asc(), Game.game_pk.asc())
        ).unique().all()
    )


def _prediction_accuracy_for_team(
    db: Session,
    games: list[Game],
) -> dict[str, Any]:
    scored = [g for g in games if _is_scored_game(g)]
    if not scored:
        return {"n_predictions": 0, "n_correct": 0, "accuracy": None}

    try:
        model = get_active_pregame_model(db)
    except Exception:
        return {"n_predictions": 0, "n_correct": 0, "accuracy": None}

    game_ids = [g.id for g in scored]
    predictions = db.scalars(
        select(Prediction).where(
            Prediction.model_version_id == model.id,
            Prediction.game_id.in_(game_ids),
        )
    ).all()
    by_game = {p.game_id: p for p in predictions}

    n_pred = 0
    n_correct = 0
    for game in scored:
        prediction = by_game.get(game.id)
        if prediction is None or prediction.home_win_proba is None:
            continue
        n_pred += 1
        predicted_home = prediction.home_win_proba >= (prediction.away_win_proba or 0.0)
        actual_home = bool(
            game.home_score is not None
            and game.away_score is not None
            and game.home_score > game.away_score
        )
        if predicted_home == actual_home:
            n_correct += 1

    return {
        "n_predictions": n_pred,
        "n_correct": n_correct,
        "accuracy": (n_correct / n_pred) if n_pred else None,
        "model_version_id": model.id,
        "run_id": model.run_id,
    }


def _monthly_trend(games: list[Game], team_id: int) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for game in games:
        if not _is_scored_game(game):
            continue
        key = f"{game.game_date.year:04d}-{game.game_date.month:02d}"
        bucket = buckets.setdefault(
            key,
            {
                "month": key,
                "games": 0,
                "wins": 0,
                "losses": 0,
                "runs_scored": 0,
                "runs_allowed": 0,
            },
        )
        rs, ra = _runs_for_against(game, team_id)
        bucket["games"] += 1
        bucket["runs_scored"] += rs
        bucket["runs_allowed"] += ra
        if _team_won(game, team_id):
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1

    rows: list[dict[str, Any]] = []
    for key in sorted(buckets):
        bucket = buckets[key]
        bucket["run_diff"] = bucket["runs_scored"] - bucket["runs_allowed"]
        rows.append(bucket)
    return rows


def _venue_splits(games: list[Game], team_id: int) -> dict[str, Any]:
    home = {"games": 0, "wins": 0, "losses": 0}
    away = {"games": 0, "wins": 0, "losses": 0}
    for game in games:
        if not _is_scored_game(game):
            continue
        side = home if game.home_team_id == team_id else away
        side["games"] += 1
        if _team_won(game, team_id):
            side["wins"] += 1
        else:
            side["losses"] += 1
    return {"home": home, "away": away}


def get_team_analytics(db: Session, team_id: int, season: int) -> dict[str, Any]:
    team = db.get(Team, team_id)
    if team is None:
        raise TeamNotFoundError(f"Team not found: id={team_id}")

    games = _load_team_games(db, team_id, season)
    scored = [g for g in games if _is_scored_game(g)]
    wins = sum(1 for g in scored if _team_won(g, team_id))
    losses = len(scored) - wins
    runs_scored = 0
    runs_allowed = 0
    for game in scored:
        rs, ra = _runs_for_against(game, team_id)
        runs_scored += rs
        runs_allowed += ra

    recent = []
    for game in reversed(scored[-10:]):
        rs, ra = _runs_for_against(game, team_id)
        opponent = game.away_team if game.home_team_id == team_id else game.home_team
        recent.append(
            {
                "game_pk": game.game_pk,
                "game_date": game.game_date,
                "opponent_abbreviation": opponent.abbreviation,
                "opponent_name": opponent.name,
                "is_home": game.home_team_id == team_id,
                "runs_scored": rs,
                "runs_allowed": ra,
                "result": "W" if _team_won(game, team_id) else "L",
            }
        )

    return {
        "team": {
            "id": team.id,
            "abbreviation": team.abbreviation,
            "name": team.name,
            "city": team.city,
        },
        "season": season,
        "record": {
            "games": len(scored),
            "wins": wins,
            "losses": losses,
            "runs_scored": runs_scored,
            "runs_allowed": runs_allowed,
            "run_diff": runs_scored - runs_allowed,
        },
        "splits": _venue_splits(games, team_id),
        "monthly_trend": _monthly_trend(games, team_id),
        "prediction_accuracy": _prediction_accuracy_for_team(db, games),
        "fangraphs": _fangraphs_team_stats(team.abbreviation, season),
        "recent_games": recent,
    }


def _event_splits_for_player(db: Session, player: Player, season: int) -> dict[str, Any]:
    """Light play-level splits from stored GameEvent payloads mentioning the player."""
    games = list(
        db.scalars(
            select(Game).where(
                Game.season == season,
                or_(
                    Game.home_probable_pitcher_id == player.id,
                    Game.away_probable_pitcher_id == player.id,
                    Game.home_team_id == player.team_id,
                    Game.away_team_id == player.team_id,
                ),
            )
        ).all()
    )
    if not games and player.team_id is None:
        games = []

    game_pks = [g.game_pk for g in games]
    if not game_pks:
        return {
            "scoring_plays_as_batter": 0,
            "scoring_plays_as_pitcher": 0,
            "rbi": 0,
            "events_scanned": 0,
        }

    events = db.scalars(
        select(GameEvent).where(GameEvent.game_pk.in_(game_pks))
    ).all()

    name = player.full_name.strip().lower()
    as_batter = 0
    as_pitcher = 0
    rbi_total = 0
    scanned = 0
    for event in events:
        payload = event.payload or {}
        scanned += 1
        batter = str(payload.get("batter_name") or "").strip().lower()
        pitcher = str(payload.get("pitcher_name") or "").strip().lower()
        is_scoring = bool(payload.get("is_scoring_play"))
        if batter == name and is_scoring:
            as_batter += 1
            rbi = payload.get("rbi")
            if isinstance(rbi, (int, float)):
                rbi_total += int(rbi)
        if pitcher == name and is_scoring:
            as_pitcher += 1

    return {
        "scoring_plays_as_batter": as_batter,
        "scoring_plays_as_pitcher": as_pitcher,
        "rbi": rbi_total,
        "events_scanned": scanned,
    }


def get_player_analytics(db: Session, player_id: int, season: int) -> dict[str, Any]:
    player = db.scalars(
        select(Player).options(joinedload(Player.team)).where(Player.id == player_id)
    ).first()
    if player is None:
        raise PlayerNotFoundError(f"Player not found: id={player_id}")

    team = player.team
    starts = list(
        db.scalars(
            select(Game)
            .options(
                joinedload(Game.home_team),
                joinedload(Game.away_team),
            )
            .where(
                Game.season == season,
                or_(
                    Game.home_probable_pitcher_id == player_id,
                    Game.away_probable_pitcher_id == player_id,
                ),
            )
            .order_by(Game.game_date.asc(), Game.game_pk.asc())
        ).unique().all()
    )

    start_rows: list[dict[str, Any]] = []
    wins = 0
    losses = 0
    for game in starts:
        is_home = game.home_probable_pitcher_id == player_id
        team_id = game.home_team_id if is_home else game.away_team_id
        opponent = game.away_team if is_home else game.home_team
        result: str | None = None
        if _is_scored_game(game):
            result = "W" if _team_won(game, team_id) else "L"
            if result == "W":
                wins += 1
            else:
                losses += 1
        start_rows.append(
            {
                "game_pk": game.game_pk,
                "game_date": game.game_date,
                "is_home": is_home,
                "opponent_abbreviation": opponent.abbreviation,
                "opponent_name": opponent.name,
                "team_score": (
                    game.home_score if is_home else game.away_score
                ),
                "opponent_score": (
                    game.away_score if is_home else game.home_score
                ),
                "result": result,
                "detailed_state": game.detailed_state,
            }
        )

    return {
        "player": {
            "id": player.id,
            "full_name": player.full_name,
            "primary_position": player.primary_position,
            "team": (
                {
                    "id": team.id,
                    "abbreviation": team.abbreviation,
                    "name": team.name,
                    "city": team.city,
                }
                if team is not None
                else None
            ),
        },
        "season": season,
        "probable_starts": {
            "games": len(starts),
            "wins": wins,
            "losses": losses,
            "win_pct": (wins / (wins + losses)) if (wins + losses) else None,
            "games_detail": start_rows,
        },
        "event_splits": _event_splits_for_player(db, player, season),
        "fangraphs": _fangraphs_pitcher_stats(
            player.full_name,
            team.abbreviation if team else None,
            season,
        ),
    }


def get_matchup_analytics(
    db: Session,
    *,
    home_team_id: int,
    away_team_id: int,
    season: int,
) -> dict[str, Any]:
    if home_team_id == away_team_id:
        raise ValueError("home_team_id and away_team_id must differ")

    home = db.get(Team, home_team_id)
    away = db.get(Team, away_team_id)
    if home is None:
        raise TeamNotFoundError(f"Team not found: id={home_team_id}")
    if away is None:
        raise TeamNotFoundError(f"Team not found: id={away_team_id}")

    # All meetings this season regardless of venue assignment in the request.
    meetings = list(
        db.scalars(
            select(Game)
            .options(joinedload(Game.home_team), joinedload(Game.away_team))
            .where(
                Game.season == season,
                or_(
                    (Game.home_team_id == home_team_id) & (Game.away_team_id == away_team_id),
                    (Game.home_team_id == away_team_id) & (Game.away_team_id == home_team_id),
                ),
            )
            .order_by(Game.game_date.asc(), Game.game_pk.asc())
        ).unique().all()
    )

    home_wins = 0
    away_wins = 0
    rows: list[dict[str, Any]] = []
    for game in meetings:
        home_team_won: bool | None = None
        if _is_scored_game(game):
            home_team_won = game.home_score > game.away_score  # type: ignore[operator]
            # Attribute win to the requested "home" side when they played.
            if game.home_team_id == home_team_id:
                if home_team_won:
                    home_wins += 1
                else:
                    away_wins += 1
            else:
                if home_team_won:
                    away_wins += 1
                else:
                    home_wins += 1
        rows.append(
            {
                "game_pk": game.game_pk,
                "game_date": game.game_date,
                "venue_home_abbreviation": game.home_team.abbreviation,
                "venue_away_abbreviation": game.away_team.abbreviation,
                "home_score": game.home_score,
                "away_score": game.away_score,
                "detailed_state": game.detailed_state,
                "winner": game.winner,
            }
        )

    home_fg = _fangraphs_team_stats(home.abbreviation, season)
    away_fg = _fangraphs_team_stats(away.abbreviation, season)
    fg_diff: dict[str, float | None] | None = None
    if home_fg and away_fg:
        def _diff(key: str) -> float | None:
            hv = home_fg.get(key)  # type: ignore[union-attr]
            av = away_fg.get(key)  # type: ignore[union-attr]
            if isinstance(hv, (int, float)) and isinstance(av, (int, float)):
                return float(hv) - float(av)
            return None

        fg_diff = {
            "wrc_plus": _diff("wrc_plus"),
            # Lower FIP is better; positive means home staff is better.
            "team_fip": (
                None
                if away_fg.get("team_fip") is None or home_fg.get("team_fip") is None
                else float(away_fg["team_fip"]) - float(home_fg["team_fip"])  # type: ignore[arg-type]
            ),
            "bullpen_fip": (
                None
                if away_fg.get("bullpen_fip") is None or home_fg.get("bullpen_fip") is None
                else float(away_fg["bullpen_fip"]) - float(home_fg["bullpen_fip"])  # type: ignore[arg-type]
            ),
            "median_starter_fip": (
                None
                if away_fg.get("median_starter_fip") is None
                or home_fg.get("median_starter_fip") is None
                else float(away_fg["median_starter_fip"])
                - float(home_fg["median_starter_fip"])  # type: ignore[arg-type]
            ),
        }

    return {
        "season": season,
        "home_team": {
            "id": home.id,
            "abbreviation": home.abbreviation,
            "name": home.name,
            "city": home.city,
        },
        "away_team": {
            "id": away.id,
            "abbreviation": away.abbreviation,
            "name": away.name,
            "city": away.city,
        },
        "head_to_head": {
            "meetings": len(meetings),
            "scored_games": home_wins + away_wins,
            "home_wins": home_wins,
            "away_wins": away_wins,
            "games": rows,
        },
        "fangraphs_home": home_fg,
        "fangraphs_away": away_fg,
        "fangraphs_diff": fg_diff,
    }
