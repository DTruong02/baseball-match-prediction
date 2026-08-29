"""Sync MLB schedule data into Postgres."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from baseball_analyze.data.mlb_client import ScheduledGame, fetch_schedule_for_date, fetch_teams
from baseball_backend.db.models import Game, Player, Team


def _team_name_and_city(team_payload: dict[str, Any], abbrev: str) -> tuple[str, str | None]:
    name = team_payload.get("name") or team_payload.get("teamName") or abbrev
    city = team_payload.get("locationName") or team_payload.get("franchiseName")
    return str(name), str(city) if city else None


def _upsert_team(
    db: Session,
    team_id: int,
    abbrev: str,
    teams_cache: dict[int, dict[str, Any]],
) -> Team:
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
    return team


def _upsert_player(
    db: Session,
    player_id: int,
    full_name: str,
    team_id: int | None,
) -> Player:
    player = db.get(Player, player_id)
    if player is None:
        player = Player(id=player_id, full_name=full_name, team_id=team_id)
        db.add(player)
    else:
        player.full_name = full_name
        player.team_id = team_id
    return player


def _upsert_game(db: Session, scheduled: ScheduledGame) -> Game:
    game = db.scalar(select(Game).where(Game.game_pk == scheduled.game_pk))
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
    }
    if game is None:
        game = Game(game_pk=scheduled.game_pk, **fields)
        db.add(game)
    else:
        for key, value in fields.items():
            setattr(game, key, value)
    return game


def sync_schedule_for_date(db: Session, game_date: str) -> int:
    """
    Fetch MLB schedule for ``game_date`` (YYYY-MM-DD) and upsert teams/games.

    Returns the number of games synced.
    """
    teams_cache = {int(team["id"]): team for team in fetch_teams()}
    scheduled_games = fetch_schedule_for_date(game_date)

    for scheduled in scheduled_games:
        _upsert_team(db, scheduled.home_team_id, scheduled.home_abbrev, teams_cache)
        _upsert_team(db, scheduled.away_team_id, scheduled.away_abbrev, teams_cache)

        if scheduled.home_probable_id and scheduled.home_probable_name:
            _upsert_player(
                db,
                scheduled.home_probable_id,
                scheduled.home_probable_name,
                scheduled.home_team_id,
            )
        if scheduled.away_probable_id and scheduled.away_probable_name:
            _upsert_player(
                db,
                scheduled.away_probable_id,
                scheduled.away_probable_name,
                scheduled.away_team_id,
            )

        _upsert_game(db, scheduled)

    db.commit()
    return len(scheduled_games)
