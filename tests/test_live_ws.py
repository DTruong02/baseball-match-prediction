"""Tests for live WebSocket fan-out and endpoints."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from baseball_backend.db.base import Base
from baseball_backend.db.models import Game, ModelVersion, Player, Team, User
from baseball_backend.db.session import get_db
from baseball_backend.main import app
from baseball_backend.routes import ws as ws_routes
from baseball_backend.security import create_access_token
from baseball_backend.services.live_cache import (
    LIVE_UPDATE_CHANNEL_PATTERN,
    parse_game_pk_from_channel,
)
from baseball_backend.services.live_ws import (
    LiveConnectionManager,
    envelope,
    is_live_payload_stale,
    postgres_live_snapshot,
    resolve_live_snapshot,
    run_live_pubsub_fanout,
)
from baseball_backend.settings import Settings, get_settings

SAMPLE_TEAMS = [
    {"id": 111, "abbreviation": "BOS", "name": "Red Sox", "city": "Boston"},
    {"id": 147, "abbreviation": "NYY", "name": "Yankees", "city": "New York"},
]


def _sample_live_payload(game_pk: int = 824239) -> dict[str, Any]:
    return {
        "game_pk": game_pk,
        "home_score": 4,
        "away_score": 2,
        "status": "Live",
        "detailed_state": "In Progress",
        "current_inning": 6,
        "inning_state": "Top",
        "is_top_inning": True,
        "outs": 1,
        "balls": 2,
        "strikes": 0,
        "events_inserted": 3,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        pool_pre_ping=False,
        poolclass=StaticPool,
    )
    jsonb_columns: list = []
    for table in (ModelVersion.__table__,):
        for column in table.columns:
            if isinstance(column.type, JSONB):
                jsonb_columns.append(column)
                column.type = JSON()
    tables = [
        User.__table__,
        Team.__table__,
        Player.__table__,
        Game.__table__,
        ModelVersion.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        for team_data in SAMPLE_TEAMS:
            session.add(Team(**team_data))
        session.add(
            Game(
                game_pk=824239,
                game_date=date(2025, 4, 6),
                season=2025,
                status="Live",
                detailed_state="In Progress",
                home_team_id=147,
                away_team_id=111,
                home_score=3,
                away_score=2,
            )
        )
        session.add(
            Game(
                game_pk=824240,
                game_date=date(2025, 4, 6),
                season=2025,
                status="Preview",
                detailed_state="Scheduled",
                home_team_id=111,
                away_team_id=147,
            )
        )
        session.commit()
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=tables)
        engine.dispose()
        for column in jsonb_columns:
            column.type = JSONB()


@pytest.fixture
def client(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    @contextmanager
    def override_db_scope() -> Iterator[Session]:
        yield db_session

    test_settings = Settings(
        secret_key="test-secret",
        redis_enabled=False,
        live_pubsub_enabled=False,
        database_url="sqlite://",
    )

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    monkeypatch.setattr(ws_routes, "db_session_scope", override_db_scope)
    get_settings.cache_clear()
    monkeypatch.setattr(
        "baseball_backend.routes.ws.get_settings",
        lambda: test_settings,
    )
    monkeypatch.setattr(
        "baseball_backend.services.live_ws.get_settings",
        lambda: test_settings,
    )

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture
def auth_token(db_session: Session) -> str:
    user = User(email="fan@example.com", hashed_password="unused")
    db_session.add(user)
    db_session.commit()
    return create_access_token(
        subject=user.email,
        secret_key="test-secret",
        expires_delta=timedelta(hours=1),
    )


def test_parse_game_pk_from_channel() -> None:
    assert parse_game_pk_from_channel("live:game:824239:updates") == 824239
    assert parse_game_pk_from_channel("live:game:abc:updates") is None
    assert parse_game_pk_from_channel("other") is None
    assert LIVE_UPDATE_CHANNEL_PATTERN == "live:game:*:updates"


def test_postgres_live_snapshot(db_session: Session) -> None:
    snapshot = postgres_live_snapshot(db_session, 824239)
    assert snapshot is not None
    assert snapshot["game_pk"] == 824239
    assert snapshot["home_score"] == 3
    assert snapshot["away_score"] == 2
    assert snapshot["current_inning"] is None


def test_postgres_live_snapshot_uses_stored_live_state(db_session: Session) -> None:
    from sqlalchemy import select

    game = db_session.scalar(select(Game).where(Game.game_pk == 824239))
    assert game is not None
    game.live_state = {
        "game_pk": 824239,
        "home_score": 3,
        "away_score": 2,
        "status": "Live",
        "detailed_state": "In Progress",
        "current_inning": 7,
        "inning_state": "Bottom",
        "is_top_inning": False,
        "outs": 2,
        "balls": 1,
        "strikes": 2,
        "events_inserted": 12,
        "updated_at": "2026-09-05T18:00:00+00:00",
    }
    db_session.commit()

    snapshot = postgres_live_snapshot(db_session, 824239)
    assert snapshot is not None
    assert snapshot["current_inning"] == 7
    assert snapshot["outs"] == 2
    assert snapshot["strikes"] == 2


def test_is_live_payload_stale() -> None:
    fresh = _sample_live_payload()
    assert is_live_payload_stale(fresh, stale_after_seconds=90) is False

    stale = _sample_live_payload()
    stale["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=10)
    ).isoformat()
    assert is_live_payload_stale(stale, stale_after_seconds=90) is True

    final_payload = _sample_live_payload()
    final_payload["status"] = "Final"
    final_payload["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).isoformat()
    assert is_live_payload_stale(final_payload, stale_after_seconds=90) is False


def test_resolve_live_snapshot_marks_stale_redis_as_degraded(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached = _sample_live_payload()
    cached["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=10)
    ).isoformat()
    monkeypatch.setattr(
        "baseball_backend.services.live_ws.get_cached_live_state",
        lambda _game_pk: cached,
    )
    monkeypatch.setattr(
        "baseball_backend.services.live_ws.get_live_feed_health",
        lambda: {"ok": True},
    )
    data, source, degraded = resolve_live_snapshot(db_session, 824239)
    assert data == cached
    assert source == "redis"
    assert degraded is True


def test_resolve_live_snapshot_marks_mlb_outage_as_degraded(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached = _sample_live_payload()
    monkeypatch.setattr(
        "baseball_backend.services.live_ws.get_cached_live_state",
        lambda _game_pk: cached,
    )
    monkeypatch.setattr(
        "baseball_backend.services.live_ws.get_live_feed_health",
        lambda: {"ok": False, "error": "MLB down"},
    )
    data, source, degraded = resolve_live_snapshot(db_session, 824239)
    assert data == cached
    assert source == "redis"
    assert degraded is True


def test_resolve_live_snapshot_prefers_redis(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached = _sample_live_payload()
    monkeypatch.setattr(
        "baseball_backend.services.live_ws.get_cached_live_state",
        lambda _game_pk: cached,
    )
    monkeypatch.setattr(
        "baseball_backend.services.live_ws.get_live_feed_health",
        lambda: {"ok": True},
    )
    data, source, degraded = resolve_live_snapshot(db_session, 824239)
    assert data == cached
    assert source == "redis"
    assert degraded is False


def test_resolve_live_snapshot_falls_back_to_postgres(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "baseball_backend.services.live_ws.get_cached_live_state",
        lambda _game_pk: None,
    )
    data, source, degraded = resolve_live_snapshot(db_session, 824239)
    assert data is not None
    assert data["home_score"] == 3
    assert source == "postgres"
    assert degraded is True


def test_connection_manager_broadcasts_to_game_and_slate() -> None:
    async def _run() -> None:
        manager = LiveConnectionManager()
        game_ws = MagicMock()
        game_ws.send_json = AsyncMock()
        slate_ws = MagicMock()
        slate_ws.send_json = AsyncMock()
        game_ws.accept = AsyncMock()
        slate_ws.accept = AsyncMock()

        await manager.connect_game(game_ws, 824239)
        await manager.connect_slate(slate_ws, "2025-04-06", {824239, 824240})

        message = envelope("update", game_pk=824239, data=_sample_live_payload())
        await manager.broadcast_game_update(824239, message)

        game_ws.send_json.assert_awaited_once_with(message)
        slate_ws.send_json.assert_awaited_once_with(message)

    asyncio.run(_run())


def test_run_live_pubsub_fanout_forwards_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run() -> None:
        manager = LiveConnectionManager()
        received: list[dict[str, Any]] = []

        class FakeSocket:
            async def accept(self) -> None:
                return None

            async def send_json(self, message: dict[str, Any]) -> None:
                received.append(message)

        socket = FakeSocket()
        await manager.connect_game(socket, 824239)

        payload = _sample_live_payload()
        messages: list[dict[str, Any] | None] = [
            {
                "type": "pmessage",
                "channel": "live:game:824239:updates",
                "data": json.dumps(payload),
            },
            None,
        ]

        class FakePubSub:
            def psubscribe(self, *_args: Any) -> None:
                return None

            def punsubscribe(self, *_args: Any) -> None:
                return None

            def close(self) -> None:
                return None

            def get_message(self, timeout: float = 0.0) -> dict[str, Any] | None:
                if messages:
                    return messages.pop(0)
                return None

        class FakeRedis:
            def pubsub(self, ignore_subscribe_messages: bool = False) -> FakePubSub:
                return FakePubSub()

        stop_event = asyncio.Event()

        async def stop_soon() -> None:
            await asyncio.sleep(0.05)
            stop_event.set()

        monkeypatch.setattr(
            "baseball_backend.services.live_ws.get_redis_client",
            lambda: FakeRedis(),
        )
        monkeypatch.setattr(
            "baseball_backend.services.live_ws.get_settings",
            lambda: Settings(redis_enabled=True, live_pubsub_enabled=True),
        )

        await asyncio.gather(
            run_live_pubsub_fanout(manager, stop_event=stop_event),
            stop_soon(),
        )

        assert len(received) == 1
        assert received[0]["type"] == "update"
        assert received[0]["game_pk"] == 824239
        assert received[0]["data"]["home_score"] == 4

    asyncio.run(_run())


def test_ws_game_requires_auth(client: TestClient) -> None:
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/games/824239"):
            pass


def test_ws_game_sends_postgres_snapshot(
    client: TestClient,
    auth_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "baseball_backend.services.live_ws.get_cached_live_state",
        lambda _game_pk: None,
    )
    with client.websocket_connect(f"/ws/games/824239?token={auth_token}") as ws:
        message = ws.receive_json()
        assert message["type"] == "snapshot"
        assert message["game_pk"] == 824239
        assert message["source"] == "postgres"
        assert message["degraded"] is True
        assert message["data"]["home_score"] == 3
        assert message["data"]["away_score"] == 2


def test_ws_game_sends_redis_snapshot(
    client: TestClient,
    auth_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cached = _sample_live_payload()
    monkeypatch.setattr(
        "baseball_backend.services.live_ws.get_cached_live_state",
        lambda _game_pk: cached,
    )
    monkeypatch.setattr(
        "baseball_backend.services.live_ws.get_live_feed_health",
        lambda: {"ok": True},
    )
    with client.websocket_connect(f"/ws/games/824239?token={auth_token}") as ws:
        message = ws.receive_json()
        assert message["type"] == "snapshot"
        assert message["source"] == "redis"
        assert message["degraded"] is False
        assert message["data"]["current_inning"] == 6


def test_ws_live_slate_snapshot(
    client: TestClient,
    auth_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "baseball_backend.services.live_ws.get_cached_live_state",
        lambda _game_pk: None,
    )
    with client.websocket_connect(
        f"/ws/live?date=2025-04-06&token={auth_token}"
    ) as ws:
        message = ws.receive_json()
        assert message["type"] == "slate_snapshot"
        assert message["date"] == "2025-04-06"
        assert len(message["data"]) == 2
        game_pks = {item["game_pk"] for item in message["data"]}
        assert game_pks == {824239, 824240}
