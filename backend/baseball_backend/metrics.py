"""In-process Prometheus metrics for API latency, errors, cache, and workers."""

from __future__ import annotations

import time
from typing import Any

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# Dedicated registry so tests can reset cleanly without touching the global default.
REGISTRY = CollectorRegistry()

REQUEST_COUNT = Counter(
    "baseball_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
    registry=REGISTRY,
)
REQUEST_LATENCY = Histogram(
    "baseball_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=REGISTRY,
)
REQUEST_ERRORS = Counter(
    "baseball_http_errors_total",
    "HTTP responses with status >= 500",
    ["method", "path"],
    registry=REGISTRY,
)
LIVE_CACHE_LOOKUPS = Counter(
    "baseball_live_cache_lookups_total",
    "Live state cache lookups by result",
    ["result"],
    registry=REGISTRY,
)
LIVE_WORKER_LAG = Gauge(
    "baseball_live_worker_lag_seconds",
    "Seconds since the live worker last completed a poll cycle",
    registry=REGISTRY,
)
LIVE_WORKER_CYCLE = Gauge(
    "baseball_live_worker_last_cycle_seconds",
    "Duration of the live worker's last poll cycle",
    registry=REGISTRY,
)
NOTIFICATION_WORKER_LAG = Gauge(
    "baseball_notification_worker_lag_seconds",
    "Seconds since the notification worker last completed a cycle",
    registry=REGISTRY,
)
NOTIFICATION_PENDING = Gauge(
    "baseball_notification_pending_emails",
    "Pending email notifications observed by the last worker cycle",
    registry=REGISTRY,
)
WS_CONNECTIONS = Gauge(
    "baseball_ws_connections",
    "Active WebSocket connections",
    ["scope"],
    registry=REGISTRY,
)

# Paths that should not inflate high-cardinality labels.
_SKIP_PATH_PREFIXES = ("/metrics",)


def normalize_path(path: str) -> str:
    """Collapse dynamic path segments for metric labels."""
    if path.startswith("/ws/games/"):
        return "/ws/games/{game_pk}"
    if path.startswith("/games/") and path.count("/") >= 2:
        # /games/{game_pk} or /games/{game_pk}/...
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[1].isdigit():
            rest = "/".join(parts[2:])
            return f"/games/{{game_pk}}" + (f"/{rest}" if rest else "")
    if path.startswith("/predictions/") and path.count("/") >= 2:
        return "/predictions/{game_pk}"
    return path


def observe_request(*, method: str, path: str, status_code: int, duration: float) -> None:
    label_path = normalize_path(path)
    if any(path.startswith(prefix) for prefix in _SKIP_PATH_PREFIXES):
        return
    REQUEST_COUNT.labels(method=method, path=label_path, status=str(status_code)).inc()
    REQUEST_LATENCY.labels(method=method, path=label_path).observe(duration)
    if status_code >= 500:
        REQUEST_ERRORS.labels(method=method, path=label_path).inc()


def record_live_cache_hit() -> None:
    LIVE_CACHE_LOOKUPS.labels(result="hit").inc()


def record_live_cache_miss() -> None:
    LIVE_CACHE_LOOKUPS.labels(result="miss").inc()


def set_live_worker_lag(seconds: float | None) -> None:
    if seconds is None:
        LIVE_WORKER_LAG.set(-1)
    else:
        LIVE_WORKER_LAG.set(max(0.0, seconds))


def set_live_worker_cycle(seconds: float) -> None:
    LIVE_WORKER_CYCLE.set(max(0.0, seconds))


def set_notification_worker_lag(seconds: float | None) -> None:
    if seconds is None:
        NOTIFICATION_WORKER_LAG.set(-1)
    else:
        NOTIFICATION_WORKER_LAG.set(max(0.0, seconds))


def set_notification_pending(count: int) -> None:
    NOTIFICATION_PENDING.set(max(0, count))


def set_ws_connections(*, scope: str, count: int) -> None:
    WS_CONNECTIONS.labels(scope=scope).set(max(0, count))


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


class RequestTimer:
    """Context helper for middleware timing."""

    def __init__(self) -> None:
        self._start = time.perf_counter()

    def duration(self) -> float:
        return time.perf_counter() - self._start


def metrics_snapshot() -> dict[str, Any]:
    """Small JSON-friendly summary used by readiness diagnostics."""
    return {
        "live_cache_hits": _counter_value(LIVE_CACHE_LOOKUPS, {"result": "hit"}),
        "live_cache_misses": _counter_value(LIVE_CACHE_LOOKUPS, {"result": "miss"}),
        "http_5xx": _sum_counter(REQUEST_ERRORS),
    }


def _counter_value(counter: Counter, labels: dict[str, str]) -> float:
    try:
        return float(counter.labels(**labels)._value.get())  # noqa: SLF001
    except Exception:
        return 0.0


def _sum_counter(counter: Counter) -> float:
    total = 0.0
    for metric in counter.collect():
        for sample in metric.samples:
            if sample.name.endswith("_total"):
                total += float(sample.value)
    return total
