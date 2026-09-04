"""Redis cache and pub/sub for live game state."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from baseball_backend.redis_client import get_redis_client
from baseball_backend.services.live_normalize import GameLiveState
from baseball_backend.settings import get_settings

logger = logging.getLogger(__name__)

LIVE_STATE_KEY_PREFIX = "live:game:"
LIVE_UPDATE_CHANNEL_SUFFIX = ":updates"


def live_state_key(game_pk: int) -> str:
    return f"{LIVE_STATE_KEY_PREFIX}{game_pk}"


def live_update_channel(game_pk: int) -> str:
    return f"{LIVE_STATE_KEY_PREFIX}{game_pk}{LIVE_UPDATE_CHANNEL_SUFFIX}"


def serialize_live_state(
    game_pk: int,
    state: GameLiveState,
    *,
    events_inserted: int = 0,
) -> dict[str, Any]:
    return {
        "game_pk": game_pk,
        "home_score": state.home_score,
        "away_score": state.away_score,
        "status": state.status,
        "detailed_state": state.detailed_state,
        "current_inning": state.current_inning,
        "inning_state": state.inning_state,
        "is_top_inning": state.is_top_inning,
        "outs": state.outs,
        "balls": state.balls,
        "strikes": state.strikes,
        "events_inserted": events_inserted,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


class LiveStateCache:
    """Write-through cache for current live game state with optional pub/sub."""

    def __init__(
        self,
        redis_client: Any,
        *,
        ttl_completed_seconds: int,
        pubsub_enabled: bool,
    ) -> None:
        self._redis = redis_client
        self._ttl_completed_seconds = ttl_completed_seconds
        self._pubsub_enabled = pubsub_enabled

    def store(
        self,
        game_pk: int,
        state: GameLiveState,
        *,
        events_inserted: int = 0,
    ) -> dict[str, Any]:
        payload = serialize_live_state(
            game_pk,
            state,
            events_inserted=events_inserted,
        )
        encoded = json.dumps(payload)
        key = live_state_key(game_pk)

        if state.status == "Final":
            self._redis.setex(key, self._ttl_completed_seconds, encoded)
        else:
            self._redis.set(key, encoded)

        if self._pubsub_enabled:
            self._redis.publish(live_update_channel(game_pk), encoded)

        return payload

    def get(self, game_pk: int) -> dict[str, Any] | None:
        raw = self._redis.get(live_state_key(game_pk))
        if raw is None:
            return None
        return json.loads(raw)


@lru_cache
def get_live_state_cache() -> LiveStateCache | None:
    client = get_redis_client()
    if client is None:
        return None
    settings = get_settings()
    return LiveStateCache(
        client,
        ttl_completed_seconds=settings.live_cache_ttl_completed_seconds,
        pubsub_enabled=settings.live_pubsub_enabled,
    )


def cache_live_state(
    game_pk: int,
    state: GameLiveState,
    *,
    events_inserted: int = 0,
) -> dict[str, Any] | None:
    """
    Persist live state to Redis and optionally publish an update.

    Failures are logged and swallowed so ingestion can continue without Redis.
    """
    cache = get_live_state_cache()
    if cache is None:
        return None
    try:
        return cache.store(game_pk, state, events_inserted=events_inserted)
    except Exception:
        logger.exception("Failed to cache live state for game_pk=%s", game_pk)
        return None
