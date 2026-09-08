"""Tests for team/player/matchup analytics APIs."""

from __future__ import annotations

from collections.abc import Generator
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from baseball_backend.db.base import Base
from baseball_backend.db.models import (
    Game,
    GameEvent,
    ModelVersion,
    Player,
    Prediction,
    Team,
    User,
)
from baseball_backend.db.session import get_db
from baseball_backend.main import app

SAMPLE_TEAMS = [
    {"id": 111, "abbreviation": "BOS", "name": "Red Sox", "city": "Boston"},
    {"id": 147, "abbreviation": "NYY", "name": "Yankees", "city": "New York"},
]
SAMPLE_PITCHERS = [
    {"id": 669203, "full_name": "Corbin Burnes", "team_id": 111, "primary_position": "P"},
    {"id": 592866, "full_name": "Trevor Williams", "team_id": 147, "primary_position": "P"},
]


def _final_game(
    *,
    game_pk: int,
    game_date: date,
    home_team_id: int,
    away_team_id: int,
    home_score: int,
    away_score: int,
    home_pitcher_id: int | None = None,
    away_pitcher_id: int | None = None,
) -> Game:
    winner = "home" if home_score > away_score else "away"
    return Game(
        game_pk=game_pk,
        game_date=game_date,
        season=2025,
        status="Final",
        detailed_state="Final",
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        venue_id=3313,
        venue_name="Yankee Stadium",
        home_probable_pitcher_id=home_pitcher_id,
        away_probable_pitcher_id=away_pitcher_id,
        home_score=home_score,
        away_score=away_score,
        winner=winner,
    )


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    jsonb_columns: list = []
    for table in (ModelVersion.__table__, Prediction.__table__, GameEvent.__table__):
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
        Prediction.__table__,
        GameEvent.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        for team_data in SAMPLE_TEAMS:
            session.add(Team(**team_data))
        for pitcher_data in SAMPLE_PITCHERS:
            session.add(Player(**pitcher_data))
        session.add(
            _final_game(
                game_pk=778001,
                game_date=date(2025, 4, 6),
                home_team_id=147,
                away_team_id=111,
                home_score=5,
                away_score=3,
                home_pitcher_id=592866,
                away_pitcher_id=669203,
            )
        )
        session.add(
            _final_game(
                game_pk=778002,
                game_date=date(2025, 5, 10),
                home_team_id=111,
                away_team_id=147,
                home_score=2,
                away_score=7,
                home_pitcher_id=669203,
                away_pitcher_id=592866,
            )
        )
        session.add(
            GameEvent(
                game_pk=778001,
                event_id="play-1",
                type="play",
                payload={
                    "is_scoring_play": True,
                    "batter_name": "Corbin Burnes",
                    "pitcher_name": "Trevor Williams",
                    "rbi": 2,
                },
                sequence=1,
            )
        )
        session.add(
            ModelVersion(
                run_id="analytics-test-run",
                kind="pregame",
                status="active",
                artifact_path="/tmp/model.joblib",
                feature_columns=["diff_wrc_plus"],
                metrics={},
            )
        )
        session.flush()
        model = session.query(ModelVersion).one()
        game = session.query(Game).filter_by(game_pk=778001).one()
        session.add(
            Prediction(
                game_id=game.id,
                model_version_id=model.id,
                home_win_proba=0.62,
                away_win_proba=0.38,
                features={"diff_wrc_plus": 5.0},
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
    client.post("/auth/register", json={"email": "fan@example.com", "password": "secretpass"})
    login = client.post(
        "/auth/login",
        data={"username": "fan@example.com", "password": "secretpass"},
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_team_analytics_requires_auth(client: TestClient) -> None:
    response = client.get("/teams/111/analytics", params={"season": 2025})
    assert response.status_code == 401


def test_team_analytics_monthly_trend(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    with patch(
        "baseball_backend.services.analytics_service._fangraphs_team_stats",
        return_value={
            "fangraphs_team": "BOS",
            "wrc_plus": 110.0,
            "team_fip": 3.9,
            "bullpen_fip": 4.1,
            "median_starter_fip": 3.7,
        },
    ):
        response = client.get(
            "/teams/111/analytics",
            params={"season": 2025},
            headers=auth_headers,
        )
    assert response.status_code == 200
    body = response.json()
    assert body["team"]["abbreviation"] == "BOS"
    assert body["record"]["games"] == 2
    assert body["record"]["wins"] == 0
    assert body["record"]["losses"] == 2
    assert len(body["monthly_trend"]) == 2
    assert body["monthly_trend"][0]["month"] == "2025-04"
    assert body["fangraphs"]["wrc_plus"] == 110.0
    assert body["prediction_accuracy"]["n_predictions"] == 1
    assert body["prediction_accuracy"]["n_correct"] == 1


def test_player_analytics_probable_starts(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    with patch(
        "baseball_backend.services.analytics_service._fangraphs_pitcher_stats",
        return_value={
            "fangraphs_name": "Corbin Burnes",
            "fangraphs_team": "BOS",
            "fip": 3.45,
            "era": 3.20,
            "ip": 80.0,
            "gs": 14.0,
            "k_per_9": 9.1,
            "bb_per_9": 2.4,
        },
    ):
        response = client.get(
            "/players/669203/analytics",
            params={"season": 2025},
            headers=auth_headers,
        )
    assert response.status_code == 200
    body = response.json()
    assert body["player"]["full_name"] == "Corbin Burnes"
    assert body["probable_starts"]["games"] == 2
    assert body["probable_starts"]["losses"] == 2
    assert body["event_splits"]["scoring_plays_as_batter"] == 1
    assert body["event_splits"]["rbi"] == 2
    assert body["fangraphs"]["fip"] == 3.45


def test_matchup_analytics(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    with patch(
        "baseball_backend.services.analytics_service._fangraphs_team_stats",
        side_effect=lambda abbr, season: {
            "fangraphs_team": abbr,
            "wrc_plus": 105.0 if abbr == "NYY" else 100.0,
            "team_fip": 3.8 if abbr == "NYY" else 4.0,
            "bullpen_fip": 4.0,
            "median_starter_fip": 3.6,
        },
    ):
        response = client.get(
            "/analytics/matchup",
            params={"home_team_id": 147, "away_team_id": 111, "season": 2025},
            headers=auth_headers,
        )
    assert response.status_code == 200
    body = response.json()
    assert body["head_to_head"]["meetings"] == 2
    assert body["head_to_head"]["home_wins"] == 2
    assert body["fangraphs_diff"]["wrc_plus"] == 5.0


def test_matchup_rejects_same_team(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.get(
        "/analytics/matchup",
        params={"home_team_id": 147, "away_team_id": 147, "season": 2025},
        headers=auth_headers,
    )
    assert response.status_code == 422


def test_team_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get(
        "/teams/999/analytics",
        params={"season": 2025},
        headers=auth_headers,
    )
    assert response.status_code == 404
