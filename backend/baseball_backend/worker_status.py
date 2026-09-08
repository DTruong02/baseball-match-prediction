"""Worker heartbeat keys in Redis for lag / readiness checks."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from baseball_backend.redis_client import get_redis_client

logger = logging.getLogger(__name__)

LIVE_WORKER_HEARTBEAT_KEY = "worker:live:heartbeat"
NOTIFICATION_WORKER_HEARTBEAT_KEY = "worker:notification:heartbeat"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_heartbeat(key: str, payload: dict[str, Any]) -> None:
    client = get_redis_client()
    if client is None:
        return
    try:
        client.set(key, json.dumps(payload))
    except Exception:
        logger.exception("Failed to write worker heartbeat key=%s", key)


def _read_heartbeat(key: str) -> dict[str, Any] | None:
    client = get_redis_client()
    if client is None:
        return None
    try:
        raw = client.get(key)
    except Exception:
        logger.exception("Failed to read worker heartbeat key=%s", key)
        return None
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def record_live_worker_heartbeat(
    *,
    cycle_duration_seconds: float,
    failures: int = 0,
    games_polled: int = 0,
) -> None:
    _write_heartbeat(
        LIVE_WORKER_HEARTBEAT_KEY,
        {
            "updated_at": _now_iso(),
            "cycle_duration_seconds": cycle_duration_seconds,
            "failures": failures,
            "games_polled": games_polled,
        },
    )


def record_notification_worker_heartbeat(
    *,
    cycle_duration_seconds: float,
    processed: int = 0,
    delivered: int = 0,
    failed: int = 0,
    pending: int = 0,
) -> None:
    _write_heartbeat(
        NOTIFICATION_WORKER_HEARTBEAT_KEY,
        {
            "updated_at": _now_iso(),
            "cycle_duration_seconds": cycle_duration_seconds,
            "processed": processed,
            "delivered": delivered,
            "failed": failed,
            "pending": pending,
        },
    )


def get_live_worker_heartbeat() -> dict[str, Any] | None:
    return _read_heartbeat(LIVE_WORKER_HEARTBEAT_KEY)


def get_notification_worker_heartbeat() -> dict[str, Any] | None:
    return _read_heartbeat(NOTIFICATION_WORKER_HEARTBEAT_KEY)


def heartbeat_lag_seconds(heartbeat: dict[str, Any] | None) -> float | None:
    """Seconds since ``updated_at``, or ``None`` when unknown."""
    if not heartbeat:
        return None
    raw = heartbeat.get("updated_at")
    if not isinstance(raw, str):
        return None
    try:
        updated = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - updated).total_seconds())
