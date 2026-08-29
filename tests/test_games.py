"""Tests for game schedule API routes."""

from collections.abc import Generator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from baseball_backend.db.base import Base
from baseball_backend.db.models import Game, Player, Team, User
from baseball_backend.db.session import get_db
from baseball_backend.main import app

SAMPLE_TEAMS = [
    {"id": 111, "abbreviation": "BOS", "name": "Red Sox", "city": "Boston"},
    {"id": 147, "abbreviation": "NYY", "name": "Yankees", "city": "New York"},
]
SAMPLE_PITCHERS = [
    {"id": 669203, "full_name": "Corbin Burnes", "team_id": 111},
    {"id": 592866, "full_name": "Trevor Williams", "team_id": 147},
]


def _make_sample_game() -> Game:
    return Game(
        game_pk=778001,
        game_date=date(2025, 4, 6),
        season=2025,
        status="Preview",
        detailed_state="Scheduled",
        home_team_id=147,
        away_team_id=111,
        venue_id=3313,
        venue_name="Yankee Stadium",
        home_probable_pitcher_id=592866,
        away_probable_pitcher_id=669203,
    )


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [User.__table__, Team.__table__, Player.__table__, Game.__table__]
    Base.metadata.create_all(bind=engine, tables=tables)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        for team_data in SAMPLE_TEAMS:
            session.add(Team(**team_data))
        for pitcher_data in SAMPLE_PITCHERS:
            session.add(Player(**pitcher_data))
        session.add(_make_sample_game())
        session.commit()
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=tables)
        engine.dispose()


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
    client.post("/auth/register", json={"email": "fan@example.com", "password": "secretpass"})
    login = client.post(
        "/auth/login",
        data={"username": "fan@example.com", "password": "secretpass"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_list_games_requires_auth(client: TestClient) -> None:
    response = client.get("/games", params={"date": "2025-04-06"})
    assert response.status_code == 401


def test_list_games_returns_schedule(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/games", params={"date": "2025-04-06"}, headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    game = body[0]
    assert game["game_pk"] == 778001
    assert game["home_team"]["abbreviation"] == "NYY"
    assert game["away_team"]["abbreviation"] == "BOS"
    assert game["home_probable_pitcher"]["full_name"] == "Trevor Williams"


def test_get_game_by_pk(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/games/778001", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["venue_name"] == "Yankee Stadium"


def test_get_game_returns_404_for_missing_game(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.get("/games/999999", headers=auth_headers)
    assert response.status_code == 404


def test_get_prediction_returns_null(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get("/predictions/778001", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() is None
