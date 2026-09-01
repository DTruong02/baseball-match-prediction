"""Tests for prediction scoring and model performance API."""

from __future__ import annotations

from collections.abc import Generator
from datetime import date
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import JSON, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from baseball_analyze.features import FEATURE_COLUMNS
from baseball_analyze.models.artifacts import build_manifest, save_versioned_run
from baseball_backend.db.base import Base
from baseball_backend.db.models import Game, ModelVersion, Player, Prediction, Team, User
from baseball_backend.db.session import get_db
from baseball_backend.main import app
from baseball_backend.services.model_registry import register_model_from_run
from baseball_backend.services.performance_service import (
    compute_calibration_buckets,
    compute_model_performance,
    score_and_store_active_model,
)

RUN_ID = "20260824T200812Z_runaaaaa"
SAMPLE_TEAMS = [
    {"id": 111, "abbreviation": "BOS", "name": "Red Sox", "city": "Boston"},
    {"id": 147, "abbreviation": "NYY", "name": "Yankees", "city": "New York"},
]
SAMPLE_PITCHERS = [
    {"id": 669203, "full_name": "Corbin Burnes", "team_id": 111},
    {"id": 592866, "full_name": "Trevor Williams", "team_id": 147},
]


def _tiny_fitted_pipeline() -> Pipeline:
    X = np.array([[0.0], [1.0], [0.1], [0.9]])
    y = np.array([0, 1, 0, 1])
    pipe = Pipeline([("scale", StandardScaler()), ("clf", LogisticRegression(max_iter=200))])
    pipe.fit(X, y)
    return pipe


def _write_run(artifacts_root: Path, run_id: str) -> Path:
    metrics = {"accuracy": 0.55, "roc_auc": 0.58, "log_loss": 0.68, "brier": 0.24}
    manifest = build_manifest(
        run_id=run_id,
        seasons=[2023, 2024],
        val_seasons=[2024],
        split_type="time",
        train_rows=100,
        val_rows=40,
        max_games=200,
        test_size=0.25,
        hyperparameters={
            "calibrate": False,
            "class_weight": "balanced",
            "c_grid": [1.0],
            "best_C": 1.0,
        },
        git_hash="abc123",
    )
    return save_versioned_run(
        model=_tiny_fitted_pipeline(),
        metrics=metrics,
        manifest=manifest,
        artifacts_root=artifacts_root,
        convenience_out=artifacts_root / "model.joblib",
        run_id=run_id,
    )


def _make_final_game(
    *,
    game_pk: int,
    home_score: int,
    away_score: int,
    winner: str,
) -> Game:
    return Game(
        game_pk=game_pk,
        game_date=date(2025, 4, 6),
        season=2025,
        status="Final",
        detailed_state="Final",
        home_team_id=147,
        away_team_id=111,
        venue_id=3313,
        venue_name="Yankee Stadium",
        home_probable_pitcher_id=592866,
        away_probable_pitcher_id=669203,
        home_score=home_score,
        away_score=away_score,
        winner=winner,
    )


@pytest.fixture
def artifacts_root(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    _write_run(root, RUN_ID)
    return root


@pytest.fixture
def db_session(artifacts_root: Path) -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    jsonb_columns: list = []
    for table in (ModelVersion.__table__, Prediction.__table__):
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
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        for team_data in SAMPLE_TEAMS:
            session.add(Team(**team_data))
        for pitcher_data in SAMPLE_PITCHERS:
            session.add(Player(**pitcher_data))

        home_win = _make_final_game(game_pk=778001, home_score=5, away_score=3, winner="NYY")
        away_win = _make_final_game(game_pk=778002, home_score=2, away_score=4, winner="BOS")
        session.add_all([home_win, away_win])
        session.flush()

        register_model_from_run(
            session,
            RUN_ID,
            artifacts_root=artifacts_root,
            activate=True,
        )
        model_version = session.scalar(select(ModelVersion))
        assert model_version is not None

        session.add_all(
            [
                Prediction(
                    game_id=home_win.id,
                    model_version_id=model_version.id,
                    home_win_proba=0.7,
                    away_win_proba=0.3,
                    features={column: 0.1 for column in FEATURE_COLUMNS},
                ),
                Prediction(
                    game_id=away_win.id,
                    model_version_id=model_version.id,
                    home_win_proba=0.4,
                    away_win_proba=0.6,
                    features={column: 0.2 for column in FEATURE_COLUMNS},
                ),
            ]
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
def api_client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(api_client: TestClient) -> dict[str, str]:
    api_client.post(
        "/auth/register",
        json={"email": "scorer@example.com", "password": "secretpass"},
    )
    login = api_client.post(
        "/auth/login",
        data={"username": "scorer@example.com", "password": "secretpass"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_compute_calibration_buckets() -> None:
    y_true = np.array([1, 0, 1, 0, 1, 0])
    proba_home = np.array([0.9, 0.1, 0.8, 0.2, 0.55, 0.45])
    buckets = compute_calibration_buckets(y_true, proba_home, n_buckets=2)
    assert len(buckets) == 2
    assert buckets[0]["n"] + buckets[1]["n"] == 6


def test_compute_model_performance(db_session: Session) -> None:
    performance = compute_model_performance(db_session)
    assert performance["n_games"] == 2
    assert performance["accuracy"] == 1.0
    assert performance["log_loss"] is not None
    assert len(performance["calibration_buckets"]) == 10


def test_compute_model_performance_filters_by_team(db_session: Session) -> None:
    performance = compute_model_performance(db_session, team_abbrev="BOS")
    assert performance["n_games"] == 2
    assert performance["accuracy"] == 1.0


def test_compute_model_performance_filters_by_season(db_session: Session) -> None:
    performance = compute_model_performance(db_session, season=2024)
    assert performance["n_games"] == 0


def test_compute_model_performance_filters_by_confidence_band(db_session: Session) -> None:
    performance = compute_model_performance(
        db_session,
        confidence_min=0.65,
        confidence_max=0.75,
    )
    assert performance["n_games"] == 1


def test_score_and_store_active_model(db_session: Session) -> None:
    model_version = score_and_store_active_model(db_session)
    assert model_version.production_metrics is not None
    assert model_version.production_metrics["n_games"] == 2
    assert model_version.production_metrics["accuracy"] == 1.0
    assert "computed_at" in model_version.production_metrics


def test_model_performance_api_requires_auth(api_client: TestClient) -> None:
    response = api_client.get("/model/performance")
    assert response.status_code == 401


def test_model_performance_api_returns_metrics(
    api_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = api_client.get("/model/performance", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["n_games"] == 2
    assert body["accuracy"] == 1.0
    assert body["run_id"] == RUN_ID
    assert len(body["calibration_buckets"]) == 10


def test_model_performance_api_team_filter(
    api_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = api_client.get("/model/performance?team=BOS", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["n_games"] == 2


def test_model_performance_api_confidence_band_filter(
    api_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = api_client.get(
        "/model/performance?confidence_band=0.65-0.75",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["n_games"] == 1


def test_model_performance_api_invalid_confidence_band(
    api_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = api_client.get(
        "/model/performance?confidence_band=invalid",
        headers=auth_headers,
    )
    assert response.status_code == 422
