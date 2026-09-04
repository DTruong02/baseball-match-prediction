"""Tests for Redis live state cache."""

from __future__ import annotations

import json
from typing import Any

import pytest

from baseball_backend.services.live_cache import (
    LiveStateCache,
    live_state_key,
    live_update_channel,
    serialize_live_state,
)
from baseball_backend.services.live_normalize import GameLiveState


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.published: list[tuple[str, str]] = []

    def set(self, key: str, value: str) -> None:
        self.values[key] = value
        self.ttls.pop(key, None)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = value
        self.ttls[key] = ttl

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1


def _sample_state(*, status: str = "Live", detailed_state: str = "In Progress") -> GameLiveState:
    return GameLiveState(
        home_score=3,
        away_score=2,
        status=status,
        detailed_state=detailed_state,
        current_inning=7,
        inning_state="Bottom",
        is_top_inning=False,
        outs=1,
        balls=2,
        strikes=1,
    )


def test_live_state_key_and_channel() -> None:
    assert live_state_key(824239) == "live:game:824239"
    assert live_update_channel(824239) == "live:game:824239:updates"


def test_serialize_live_state_includes_scoreboard_fields() -> None:
    payload = serialize_live_state(824239, _sample_state(), events_inserted=2)
    assert payload["game_pk"] == 824239
    assert payload["home_score"] == 3
    assert payload["away_score"] == 2
    assert payload["current_inning"] == 7
    assert payload["events_inserted"] == 2
    assert "updated_at" in payload


def test_live_state_cache_stores_in_progress_without_ttl() -> None:
    redis = FakeRedis()
    cache = LiveStateCache(redis, ttl_completed_seconds=3600, pubsub_enabled=True)

    cache.store(824239, _sample_state(), events_inserted=1)

    key = live_state_key(824239)
    assert key in redis.values
    assert key not in redis.ttls
    stored = json.loads(redis.values[key])
    assert stored["status"] == "Live"
    assert redis.published == [(live_update_channel(824239), redis.values[key])]


def test_live_state_cache_applies_ttl_for_final_games() -> None:
    redis = FakeRedis()
    cache = LiveStateCache(redis, ttl_completed_seconds=1800, pubsub_enabled=False)

    cache.store(824239, _sample_state(status="Final", detailed_state="Final"))

    key = live_state_key(824239)
    assert redis.ttls[key] == 1800
    assert redis.published == []


def test_live_state_cache_get_round_trip() -> None:
    redis = FakeRedis()
    cache = LiveStateCache(redis, ttl_completed_seconds=3600, pubsub_enabled=False)

    cache.store(824239, _sample_state())
    loaded = cache.get(824239)

    assert loaded is not None
    assert loaded["game_pk"] == 824239
    assert loaded["outs"] == 1
    assert cache.get(999) is None


@pytest.mark.parametrize(
    ("pubsub_enabled", "expected_count"),
    [(True, 1), (False, 0)],
)
def test_live_state_cache_pubsub_toggle(
    pubsub_enabled: bool,
    expected_count: int,
) -> None:
    redis = FakeRedis()
    cache = LiveStateCache(
        redis,
        ttl_completed_seconds=3600,
        pubsub_enabled=pubsub_enabled,
    )

    cache.store(824239, _sample_state())
    assert len(redis.published) == expected_count
