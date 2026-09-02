"""Poll MLB live feeds and persist game state + play events."""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from baseball_analyze.data.mlb_client import (
    MLBAPIError,
    ScheduledGame,
    fetch_live_feed,
    fetch_schedule_for_date,
)
from baseball_backend.db.models import Game, GameEvent
from baseball_backend.services.live_normalize import (
    GameLiveState,
    NormalizedGameEvent,
    normalize_game_state,
    normalize_play_events,
)
from baseball_backend.services.schedule_sync import _FINAL_STATES

_LIVE_DETAILED_STATES = frozenset(
    {
        "In Progress",
        "Delayed",
        "Delayed Start",
        "Manager Challenge",
        "Suspended",
        "Warmup",
    }
)


def _winner_from_scores(
    home_score: int,
    away_score: int,
    home_abbrev: str,
    away_abbrev: str,
) -> str | None:
    if home_score == away_score:
        return None
    return home_abbrev if home_score > away_score else away_abbrev


def _apply_live_state(game: Game, state: GameLiveState) -> None:
    game.status = state.status
    game.detailed_state = state.detailed_state
    game.home_score = state.home_score
    game.away_score = state.away_score

    if state.detailed_state in _FINAL_STATES:
        home_abbrev = game.home_team.abbreviation if game.home_team else None
        away_abbrev = game.away_team.abbreviation if game.away_team else None
        if home_abbrev and away_abbrev:
            game.winner = _winner_from_scores(
                state.home_score,
                state.away_score,
                home_abbrev,
                away_abbrev,
            )


def _existing_event_ids(db: Session, game_pk: int) -> set[str]:
    rows = db.scalars(
        select(GameEvent.event_id).where(GameEvent.game_pk == game_pk)
    ).all()
    return set(rows)


def _insert_events(
    db: Session,
    game_pk: int,
    events: list[NormalizedGameEvent],
    existing_ids: set[str],
) -> int:
    inserted = 0
    for event in events:
        if event.event_id in existing_ids:
            continue
        db.add(
            GameEvent(
                game_pk=game_pk,
                event_id=event.event_id,
                type=event.type,
                payload=event.payload,
                sequence=event.sequence,
            )
        )
        existing_ids.add(event.event_id)
        inserted += 1
    return inserted


def sync_live_game(db: Session, game_pk: int) -> dict[str, Any]:
    """
    Fetch live feed for ``game_pk``, update ``Game`` state, append new events.

    Returns a summary dict with keys ``game_pk``, ``events_inserted``,
    ``status``, and ``detailed_state``. Raises ``MLBAPIError`` on fetch failure.
    """
    game = db.scalar(select(Game).where(Game.game_pk == game_pk))
    if game is None:
        raise ValueError(f"Game {game_pk} not found in database")

    live_feed = fetch_live_feed(game_pk)
    state = normalize_game_state(
        live_feed,
        fallback_status=game.status,
        fallback_detailed_state=game.detailed_state,
    )
    _apply_live_state(game, state)

    existing_ids = _existing_event_ids(db, game_pk)
    events = normalize_play_events(live_feed)
    inserted = _insert_events(db, game_pk, events, existing_ids)

    db.commit()
    return {
        "game_pk": game_pk,
        "events_inserted": inserted,
        "status": state.status,
        "detailed_state": state.detailed_state,
    }


def _is_live_candidate(game: Game | ScheduledGame) -> bool:
    if isinstance(game, Game):
        status = game.status
        detailed = game.detailed_state
    else:
        status = game.status
        detailed = game.detailed_state
    if status == "Live":
        return True
    return detailed in _LIVE_DETAILED_STATES


def list_live_game_pks_for_date(db: Session, game_date: str) -> list[int]:
    """
    Return game PKs that should be polled for live updates on ``game_date``.

    Combines in-progress rows already in Postgres with the MLB schedule so
    newly started games are picked up even before a full schedule re-sync.
    """
    db_games = db.scalars(
        select(Game).where(Game.game_date == date.fromisoformat(game_date))
    ).all()
    live_pks = {game.game_pk for game in db_games if _is_live_candidate(game)}

    try:
        scheduled = fetch_schedule_for_date(game_date, hydrate_probable=False)
    except MLBAPIError:
        scheduled = []

    for game in scheduled:
        if _is_live_candidate(game):
            live_pks.add(game.game_pk)

    return sorted(live_pks)


def sync_live_games_for_date(
    db: Session,
    game_date: str,
    *,
    game_delay_seconds: float = 0.5,
) -> list[dict[str, Any]]:
    """Poll all live candidates for ``game_date`` with a delay between games."""
    summaries: list[dict[str, Any]] = []
    game_pks = list_live_game_pks_for_date(db, game_date)

    for index, game_pk in enumerate(game_pks):
        if index > 0 and game_delay_seconds > 0:
            time.sleep(game_delay_seconds)
        try:
            summaries.append(sync_live_game(db, game_pk))
        except (MLBAPIError, ValueError) as exc:
            summaries.append(
                {
                    "game_pk": game_pk,
                    "events_inserted": 0,
                    "error": str(exc),
                }
            )
    return summaries
