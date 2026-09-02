"""Tests for live feed normalization."""

import json
from pathlib import Path

from baseball_backend.services.live_normalize import (
    normalize_game_state,
    normalize_play_events,
)

FIXTURE = Path(__file__).parent / "fixtures" / "live_feed_sample.json"


def test_normalize_game_state_from_fixture() -> None:
    feed = json.loads(FIXTURE.read_text(encoding="utf-8"))
    state = normalize_game_state(feed)
    assert state.status == "Final"
    assert state.detailed_state == "Final"
    assert state.home_score == 3
    assert state.away_score == 4
    assert state.current_inning is not None


def test_normalize_play_events_dedup_ids() -> None:
    feed = json.loads(FIXTURE.read_text(encoding="utf-8"))
    events = normalize_play_events(feed)
    assert len(events) == 8
    event_ids = [event.event_id for event in events]
    assert event_ids == [f"play-{index}" for index in range(8)]
    assert events[0].type == "play"
    assert events[0].payload["event"] is not None
    assert events[0].sequence == 1


def test_normalize_play_events_respects_start_sequence() -> None:
    feed = json.loads(FIXTURE.read_text(encoding="utf-8"))
    events = normalize_play_events(feed, start_sequence=100)
    assert events[0].sequence == 101
    assert events[-1].sequence == 108
