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
LIVE_UPDATE_CHANNEL_PATTERN = f"{LIVE_STATE_KEY_PREFIX}*{LIVE_UPDATE_CHANNEL_SUFFIX}"
LIVE_FEED_HEALTH_KEY = "live:feed:health"


def live_state_key(game_pk: int) -> str:
    return f"{LIVE_STATE_KEY_PREFIX}{game_pk}"


def live_update_channel(game_pk: int) -> str:
    return f"{LIVE_STATE_KEY_PREFIX}{game_pk}{LIVE_UPDATE_CHANNEL_SUFFIX}"


def parse_game_pk_from_channel(channel: str) -> int | None:
    """Extract ``game_pk`` from ``live:game:{game_pk}:updates``."""
    prefix = LIVE_STATE_KEY_PREFIX
    suffix = LIVE_UPDATE_CHANNEL_SUFFIX
    if not channel.startswith(prefix) or not channel.endswith(suffix):
        return None
    middle = channel[len(prefix) : -len(suffix)]
    try:
        return int(middle)
    except ValueError:
        return None


def serialize_live_state(
    game_pk: int,
    state: GameLiveState,
    *,
    events_inserted: int = 0,
    pitcher_id: int | None = None,
    home_win_proba: float | None = None,
    away_win_proba: float | None = None,
    model_version_id: int | None = None,
    model_run_id: str | None = None,
    wp_explanation: str | None = None,
    wp_delta_home: float | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
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
    if pitcher_id is not None:
        payload["pitcher_id"] = pitcher_id
    if home_win_proba is not None:
        payload["home_win_proba"] = home_win_proba
    if away_win_proba is not None:
        payload["away_win_proba"] = away_win_proba
    if model_version_id is not None:
        payload["model_version_id"] = model_version_id
    if model_run_id is not None:
        payload["model_run_id"] = model_run_id
    if wp_explanation is not None:
        payload["wp_explanation"] = wp_explanation
    if wp_delta_home is not None:
        payload["wp_delta_home"] = wp_delta_home
    return payload


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
        pitcher_id: int | None = None,
        home_win_proba: float | None = None,
        away_win_proba: float | None = None,
        model_version_id: int | None = None,
        model_run_id: str | None = None,
        wp_explanation: str | None = None,
        wp_delta_home: float | None = None,
    ) -> dict[str, Any]:
        payload = serialize_live_state(
            game_pk,
            state,
            events_inserted=events_inserted,
            pitcher_id=pitcher_id,
            home_win_proba=home_win_proba,
            away_win_proba=away_win_proba,
            model_version_id=model_version_id,
            model_run_id=model_run_id,
            wp_explanation=wp_explanation,
            wp_delta_home=wp_delta_home,
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

    def set_feed_health(self, *, ok: bool, error: str | None = None) -> None:
        payload = {
            "ok": ok,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "error": error,
        }
        self._redis.set(LIVE_FEED_HEALTH_KEY, json.dumps(payload))

    def get_feed_health(self) -> dict[str, Any] | None:
        raw = self._redis.get(LIVE_FEED_HEALTH_KEY)
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
    pitcher_id: int | None = None,
    home_win_proba: float | None = None,
    away_win_proba: float | None = None,
    model_version_id: int | None = None,
    model_run_id: str | None = None,
    wp_explanation: str | None = None,
    wp_delta_home: float | None = None,
) -> dict[str, Any] | None:
    """
    Persist live state to Redis and optionally publish an update.

    Failures are logged and swallowed so ingestion can continue without Redis.
    """
    cache = get_live_state_cache()
    if cache is None:
        return None
    try:
        return cache.store(
            game_pk,
            state,
            events_inserted=events_inserted,
            pitcher_id=pitcher_id,
            home_win_proba=home_win_proba,
            away_win_proba=away_win_proba,
            model_version_id=model_version_id,
            model_run_id=model_run_id,
            wp_explanation=wp_explanation,
            wp_delta_home=wp_delta_home,
        )
    except Exception:
        logger.exception("Failed to cache live state for game_pk=%s", game_pk)
        return None


def get_cached_live_state(game_pk: int) -> dict[str, Any] | None:
    """
    Read current live state from Redis.

    Failures are logged and swallowed; returns ``None`` when Redis is
    unavailable or the key is missing.
    """
    cache = get_live_state_cache()
    if cache is None:
        return None
    try:
        return cache.get(game_pk)
    except Exception:
        logger.exception("Failed to read live state for game_pk=%s", game_pk)
        return None


def set_live_feed_health(*, ok: bool, error: str | None = None) -> None:
    """Record whether the live worker can reach MLB. Failures are swallowed."""
    cache = get_live_state_cache()
    if cache is None:
        return
    try:
        cache.set_feed_health(ok=ok, error=error)
    except Exception:
        logger.exception("Failed to write live feed health")


def get_live_feed_health() -> dict[str, Any] | None:
    """Return the latest live-feed health marker, or ``None`` if unavailable."""
    cache = get_live_state_cache()
    if cache is None:
        return None
    try:
        return cache.get_feed_health()
    except Exception:
        logger.exception("Failed to read live feed health")
        return None
