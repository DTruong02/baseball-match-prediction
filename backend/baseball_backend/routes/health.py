"""Liveness, readiness, and Prometheus metrics endpoints."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from baseball_backend.db.session import get_engine
from baseball_backend.metrics import (
    metrics_snapshot,
    render_metrics,
    set_live_worker_cycle,
    set_live_worker_lag,
    set_notification_pending,
    set_notification_worker_lag,
)
from baseball_backend.redis_client import ping_redis
from baseball_backend.services.live_cache import get_live_feed_health
from baseball_backend.settings import get_settings
from baseball_backend.worker_status import (
    get_live_worker_heartbeat,
    get_notification_worker_heartbeat,
    heartbeat_lag_seconds,
)

router = APIRouter(tags=["health"])


def _ml_package_version() -> str | None:
    try:
        return version("baseball-analyze")
    except PackageNotFoundError:
        return None


def _check_database() -> tuple[bool, str | None]:
    settings = get_settings()
    if not settings.database_url:
        return False, "DATABASE_URL not configured"
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:  # noqa: BLE001 — surface any connectivity failure
        return False, str(exc)


def _live_data_degraded(*, redis_configured: bool, redis_ok: bool | None) -> bool:
    """Whether clients should show the live-data-degraded banner."""
    if redis_configured and redis_ok is False:
        return True
    feed_health = get_live_feed_health()
    if feed_health is not None and feed_health.get("ok") is False:
        return True
    return False


@router.get("/health")
def health() -> dict[str, object]:
    """Liveness probe — process is up; does not require DB/Redis."""
    settings = get_settings()
    redis_configured = settings.redis_enabled
    redis_ok = ping_redis() if redis_configured else None
    return {
        "status": "ok",
        "database_configured": bool(settings.database_url),
        "redis_configured": redis_configured,
        "redis_ok": redis_ok,
        "live_degraded": _live_data_degraded(
            redis_configured=redis_configured, redis_ok=redis_ok
        ),
        "ml_package_version": _ml_package_version(),
    }


@router.get("/ready")
def ready() -> JSONResponse:
    """
    Readiness probe — dependencies needed to serve traffic.

    Returns 200 when Postgres is reachable and Redis is healthy when enabled.
    Worker heartbeats are reported but do not fail readiness (workers may be
    scaled independently).
    """
    settings = get_settings()
    db_ok, db_error = _check_database()

    redis_configured = settings.redis_enabled
    redis_ok: bool | None
    if redis_configured:
        redis_ok = ping_redis()
    else:
        redis_ok = None

    live_hb = get_live_worker_heartbeat()
    notif_hb = get_notification_worker_heartbeat()
    live_lag = heartbeat_lag_seconds(live_hb)
    notif_lag = heartbeat_lag_seconds(notif_hb)

    set_live_worker_lag(live_lag)
    set_notification_worker_lag(notif_lag)
    if live_hb and isinstance(live_hb.get("cycle_duration_seconds"), (int, float)):
        set_live_worker_cycle(float(live_hb["cycle_duration_seconds"]))
    if notif_hb and isinstance(notif_hb.get("pending"), (int, float)):
        set_notification_pending(int(notif_hb["pending"]))

    feed_health = get_live_feed_health()
    checks = {
        "database": {"ok": db_ok, "error": db_error},
        "redis": {
            "configured": redis_configured,
            "ok": redis_ok if redis_configured else True,
        },
        "live_worker": {
            "lag_seconds": live_lag,
            "heartbeat": live_hb,
        },
        "notification_worker": {
            "lag_seconds": notif_lag,
            "heartbeat": notif_hb,
        },
        "live_feed": feed_health,
        "cache": metrics_snapshot(),
    }

    ready_ok = db_ok and (redis_ok is not False)
    body: dict[str, object] = {
        "status": "ready" if ready_ok else "not_ready",
        "checks": checks,
        "ml_package_version": _ml_package_version(),
    }
    code = status.HTTP_200_OK if ready_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=body, status_code=code)


@router.get("/metrics")
def metrics() -> Response:
    """Prometheus exposition format."""
    # Refresh worker gauges from Redis before scraping.
    live_hb = get_live_worker_heartbeat()
    notif_hb = get_notification_worker_heartbeat()
    set_live_worker_lag(heartbeat_lag_seconds(live_hb))
    set_notification_worker_lag(heartbeat_lag_seconds(notif_hb))
    if live_hb and isinstance(live_hb.get("cycle_duration_seconds"), (int, float)):
        set_live_worker_cycle(float(live_hb["cycle_duration_seconds"]))
    if notif_hb and isinstance(notif_hb.get("pending"), (int, float)):
        set_notification_pending(int(notif_hb["pending"]))

    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)
