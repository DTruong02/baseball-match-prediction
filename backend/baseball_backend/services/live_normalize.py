"""Normalize MLB live feed payloads into internal game state and events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GameLiveState:
    """Current scoreboard state derived from linescore."""

    home_score: int
    away_score: int
    status: str
    detailed_state: str
    current_inning: int | None
    inning_state: str | None
    is_top_inning: bool | None
    outs: int | None
    balls: int | None
    strikes: int | None
    on_1b: bool | None = None
    on_2b: bool | None = None
    on_3b: bool | None = None


@dataclass(frozen=True)
class NormalizedGameEvent:
    """Single deduplicated event ready for persistence."""

    event_id: str
    type: str
    sequence: int
    payload: dict[str, Any]


_FINAL_STATES = frozenset({"Final", "Game Over", "Completed Early"})


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _bases_from_offense(offense: dict[str, Any]) -> tuple[bool, bool, bool]:
    """Occupied bases from linescore offense (``onFirst`` or runner objects)."""
    return (
        bool(offense.get("onFirst") or offense.get("first")),
        bool(offense.get("onSecond") or offense.get("second")),
        bool(offense.get("onThird") or offense.get("third")),
    )


def normalize_game_state(
    live_feed: dict[str, Any],
    *,
    fallback_status: str = "Unknown",
    fallback_detailed_state: str = "Unknown",
) -> GameLiveState:
    """Extract scoreboard state from a live feed payload."""
    live_data = live_feed.get("liveData") or {}
    linescore = live_data.get("linescore") or {}
    teams = linescore.get("teams") or {}
    offense = linescore.get("offense") or {}
    on_1b, on_2b, on_3b = _bases_from_offense(offense)

    home_runs = _optional_int((teams.get("home") or {}).get("runs")) or 0
    away_runs = _optional_int((teams.get("away") or {}).get("runs")) or 0

    status_payload = (live_feed.get("gameData") or {}).get("status") or {}
    status = str(status_payload.get("abstractGameState") or fallback_status)
    detailed_state = str(status_payload.get("detailedState") or fallback_detailed_state)

    if detailed_state in _FINAL_STATES:
        status = "Final"

    return GameLiveState(
        home_score=home_runs,
        away_score=away_runs,
        status=status,
        detailed_state=detailed_state,
        current_inning=_optional_int(linescore.get("currentInning")),
        inning_state=linescore.get("inningState"),
        is_top_inning=_optional_bool(linescore.get("isTopInning")),
        outs=_optional_int(linescore.get("outs")),
        balls=_optional_int(linescore.get("balls")),
        strikes=_optional_int(linescore.get("strikes")),
        on_1b=on_1b,
        on_2b=on_2b,
        on_3b=on_3b,
    )


def _play_event_id(at_bat_index: int) -> str:
    """Stable MLB play id derived from ``about.atBatIndex`` for dedupe."""
    return f"play-{at_bat_index}"


def _normalize_play(play: dict[str, Any], sequence: int) -> NormalizedGameEvent | None:
    about = play.get("about") or {}
    at_bat_index = about.get("atBatIndex")
    if at_bat_index is None:
        return None

    result = play.get("result") or {}
    matchup = play.get("matchup") or {}
    batter = matchup.get("batter") or {}
    pitcher = matchup.get("pitcher") or {}

    payload: dict[str, Any] = {
        "inning": about.get("inning"),
        "half_inning": about.get("halfInning"),
        "is_top_inning": about.get("isTopInning"),
        "is_scoring_play": about.get("isScoringPlay"),
        "is_complete": about.get("isComplete"),
        "event": result.get("event"),
        "event_type": result.get("eventType"),
        "description": result.get("description"),
        "rbi": result.get("rbi"),
        "away_score": result.get("awayScore"),
        "home_score": result.get("homeScore"),
        "batter_id": batter.get("id"),
        "batter_name": batter.get("fullName"),
        "pitcher_id": pitcher.get("id"),
        "pitcher_name": pitcher.get("fullName"),
    }

    return NormalizedGameEvent(
        event_id=_play_event_id(int(at_bat_index)),
        type="play",
        sequence=sequence,
        payload=payload,
    )


def normalize_play_events(
    live_feed: dict[str, Any],
    *,
    start_sequence: int = 0,
) -> list[NormalizedGameEvent]:
    """
    Convert ``liveData.plays.allPlays`` into ordered, deduplicated play events.

    ``start_sequence`` is added to each play's at-bat index so callers can append
    without colliding with previously ingested rows.
    """
    live_data = live_feed.get("liveData") or {}
    plays_section = live_data.get("plays") or {}
    all_plays = plays_section.get("allPlays") or []

    events: list[NormalizedGameEvent] = []
    for play in all_plays:
        about = play.get("about") or {}
        at_bat_index = about.get("atBatIndex")
        if at_bat_index is None:
            continue
        sequence = start_sequence + int(at_bat_index) + 1
        normalized = _normalize_play(play, sequence)
        if normalized is not None:
            events.append(normalized)
    return events
