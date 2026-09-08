"""Observability endpoints and helpers (Stage 7.4)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from baseball_backend.logging_config import JsonFormatter
from baseball_backend.main import app
from baseball_backend.metrics import normalize_path, record_live_cache_hit, record_live_cache_miss
from baseball_backend.worker_status import heartbeat_lag_seconds


def test_health_returns_ok() -> None:
    client = TestClient(app)
    with (
        patch("baseball_backend.routes.health.ping_redis", return_value=True),
        patch(
            "baseball_backend.routes.health.get_live_feed_health",
            return_value={"ok": True},
        ),
    ):
        response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database_configured"] is True
    assert body["live_degraded"] is False
    assert body["ml_package_version"] is not None
    assert "x-request-id" in response.headers


def test_health_live_degraded_when_feed_unhealthy() -> None:
    client = TestClient(app)
    with (
        patch("baseball_backend.routes.health.ping_redis", return_value=True),
        patch(
            "baseball_backend.routes.health.get_live_feed_health",
            return_value={"ok": False, "error": "MLB timeout"},
        ),
    ):
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["live_degraded"] is True


def test_ready_ok_when_dependencies_healthy() -> None:
    client = TestClient(app)
    with (
        patch("baseball_backend.routes.health._check_database", return_value=(True, None)),
        patch("baseball_backend.routes.health.ping_redis", return_value=True),
        patch(
            "baseball_backend.routes.health.get_live_worker_heartbeat",
            return_value={
                "updated_at": "2099-01-01T00:00:00+00:00",
                "cycle_duration_seconds": 1.2,
                "failures": 0,
                "games_polled": 3,
            },
        ),
        patch(
            "baseball_backend.routes.health.get_notification_worker_heartbeat",
            return_value={
                "updated_at": "2099-01-01T00:00:00+00:00",
                "pending": 0,
                "cycle_duration_seconds": 0.1,
            },
        ),
        patch("baseball_backend.routes.health.get_live_feed_health", return_value={"ok": True}),
    ):
        response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["redis"]["ok"] is True
    assert "cache" in body["checks"]


def test_ready_not_ready_when_database_down() -> None:
    client = TestClient(app)
    with (
        patch(
            "baseball_backend.routes.health._check_database",
            return_value=(False, "connection refused"),
        ),
        patch("baseball_backend.routes.health.ping_redis", return_value=True),
    ):
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_metrics_exposes_prometheus_text() -> None:
    record_live_cache_hit()
    record_live_cache_miss()
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    text = response.text
    assert "baseball_http_requests_total" in text or "baseball_live_cache_lookups_total" in text
    assert "baseball_live_cache_lookups_total" in text


def test_normalize_path_collapses_dynamic_segments() -> None:
    assert normalize_path("/ws/games/12345") == "/ws/games/{game_pk}"
    assert normalize_path("/games/12345") == "/games/{game_pk}"
    assert normalize_path("/games/12345/live") == "/games/{game_pk}/live"
    assert normalize_path("/health") == "/health"


def test_json_formatter_includes_extras() -> None:
    import logging

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.game_pk = 42
    formatted = JsonFormatter().format(record)
    payload = json.loads(formatted)
    assert payload["msg"] == "hello"
    assert payload["game_pk"] == 42
    assert payload["level"] == "INFO"


def test_heartbeat_lag_seconds_none_when_missing() -> None:
    assert heartbeat_lag_seconds(None) is None
    assert heartbeat_lag_seconds({}) is None


def test_worker_heartbeat_roundtrip() -> None:
    from baseball_backend import worker_status

    fake = MagicMock()
    fake.get.return_value = None
    with patch("baseball_backend.worker_status.get_redis_client", return_value=fake):
        worker_status.record_live_worker_heartbeat(
            cycle_duration_seconds=0.5, failures=0, games_polled=2
        )
        assert fake.set.called
        key, raw = fake.set.call_args[0]
        assert key == worker_status.LIVE_WORKER_HEARTBEAT_KEY
        payload = json.loads(raw)
        assert payload["games_polled"] == 2
        assert payload["cycle_duration_seconds"] == 0.5
