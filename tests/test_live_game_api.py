"""Tests for live scoreboard and event REST endpoints (WS polling fallback)."""

from collections.abc import Generator
from datetime import date
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from baseball_backend.db.base import Base
from baseball_backend.db.models import Game, GameEvent, ModelVersion, Player, Team, User
from baseball_backend.db.session import get_db
from baseball_backend.main import app

SAMPLE_TEAMS = [
    {"id": 111, "abbreviation": "BOS", "name": "Red Sox", "city": "Boston"},
    {"id": 147, "abbreviation": "NYY", "name": "Yankees", "city": "New York"},
]


def _sample_live_payload(game_pk: int = 778001) -> dict[str, Any]:
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
        "events_inserted": 1,
        "updated_at": "2025-04-06T18:30:00+00:00",
    }


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    jsonb_columns: list = []
    for table in (ModelVersion.__table__, GameEvent.__table__):
        for column in table.columns:
            if isinstance(column.type, JSONB):
                jsonb_columns.append(column)
                column.type = JSON()
    tables = [
        User.__table__,
        Team.__table__,
        Player.__table__,
        Game.__table__,
        GameEvent.__table__,
        ModelVersion.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        for team_data in SAMPLE_TEAMS:
            session.add(Team(**team_data))
        session.add(
            Game(
                game_pk=778001,
                game_date=date(2025, 4, 6),
                season=2025,
                status="Live",
                detailed_state="In Progress",
                home_team_id=147,
                away_team_id=111,
                home_score=3,
                away_score=1,
            )
        )
        session.add(
            GameEvent(
                game_pk=778001,
                event_id="play-0",
                type="play",
                sequence=0,
                payload={
                    "inning": 1,
                    "half_inning": "top",
                    "description": "Single to center.",
                    "event": "Single",
                    "away_score": 0,
                    "home_score": 0,
                },
            )
        )
        session.add(
            GameEvent(
                game_pk=778001,
                event_id="play-1",
                type="play",
                sequence=1,
                payload={
                    "inning": 1,
                    "half_inning": "top",
                    "description": "Home run to right.",
                    "event": "Home Run",
                    "is_scoring_play": True,
                    "away_score": 1,
                    "home_score": 0,
                },
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
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    client.post("/auth/register", json={"email": "live@example.com", "password": "secretpass"})
    login = client.post(
        "/auth/login",
        data={"username": "live@example.com", "password": "secretpass"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_get_live_requires_auth(client: TestClient) -> None:
    response = client.get("/games/778001/live")
    assert response.status_code == 401


def test_get_live_from_redis_cache(client: TestClient, auth_headers: dict[str, str]) -> None:
    payload = _sample_live_payload()
    with patch(
        "baseball_backend.services.live_ws.get_cached_live_state",
        return_value=payload,
    ):
        response = client.get("/games/778001/live", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "redis"
    assert body["degraded"] is False
    assert body["data"]["current_inning"] == 6
    assert body["data"]["outs"] == 1


def test_get_live_falls_back_to_postgres(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    with patch(
        "baseball_backend.services.live_ws.get_cached_live_state",
        return_value=None,
    ):
        response = client.get("/games/778001/live", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "postgres"
    assert body["degraded"] is True
    assert body["data"]["home_score"] == 3
    assert body["data"]["away_score"] == 1
    assert body["data"]["current_inning"] is None


def test_get_live_404_for_missing_game(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.get("/games/999999/live", headers=auth_headers)
    assert response.status_code == 404


def test_list_events_ordered(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/games/778001/events", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0]["event_id"] == "play-0"
    assert body[1]["event_id"] == "play-1"
    assert body[1]["payload"]["event"] == "Home Run"


def test_list_events_404_for_missing_game(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.get("/games/999999/events", headers=auth_headers)
    assert response.status_code == 404
