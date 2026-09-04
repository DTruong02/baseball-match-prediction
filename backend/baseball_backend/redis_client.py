"""Redis connection helpers."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING

from baseball_backend.settings import get_settings

if TYPE_CHECKING:
    from redis import Redis

logger = logging.getLogger(__name__)


@lru_cache
def get_redis_client() -> Redis | None:
    """Return a shared Redis client, or ``None`` when Redis is disabled."""
    settings = get_settings()
    if not settings.redis_enabled:
        return None

    try:
        import redis
    except ImportError:
        logger.warning("redis package is not installed; live cache disabled")
        return None

    return redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.redis_connect_timeout_seconds,
    )


def ping_redis() -> bool:
    """Return whether Redis is reachable."""
    client = get_redis_client()
    if client is None:
        return False
    try:
        return bool(client.ping())
    except Exception:
        logger.exception("Redis ping failed")
        return False
