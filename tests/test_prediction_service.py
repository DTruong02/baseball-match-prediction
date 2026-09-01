"""Tests for prediction service."""

from __future__ import annotations

from collections.abc import Generator
from datetime import date
from pathlib import Path
from unittest.mock import patch

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
from baseball_backend.services.prediction_service import (
    GameNotFoundError,
    generate_missing_predictions_for_date,
    generate_prediction_for_game,
    get_prediction_for_game_pk,
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
MOCK_PREDICTION = {
    "game_pk": 778001,
    "season": 2025,
    "home_fg": "NYY",
    "away_fg": "BOS",
    "home_win_proba": 0.62,
    "away_win_proba": 0.38,
    "features": {column: 0.1 for column in FEATURE_COLUMNS},
    "model_version": RUN_ID,
    "notes": ["using probable starters"],
}


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


def _make_sample_game(*, detailed_state: str = "Scheduled") -> Game:
    return Game(
        game_pk=778001,
        game_date=date(2025, 4, 6),
        season=2025,
        status="Preview",
        detailed_state=detailed_state,
        home_team_id=147,
        away_team_id=111,
        venue_id=3313,
        venue_name="Yankee Stadium",
        home_probable_pitcher_id=592866,
        away_probable_pitcher_id=669203,
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
        session.add(_make_sample_game())
        register_model_from_run(
            session,
            RUN_ID,
            artifacts_root=artifacts_root,
            activate=True,
        )
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=tables)
        engine.dispose()
        for column in jsonb_columns:
            column.type = JSONB()


@patch(
    "baseball_backend.services.prediction_service.predict_game",
    return_value=MOCK_PREDICTION,
)
def test_generate_prediction_for_game_persists_row(
    _mock_predict: object,
    db_session: Session,
) -> None:
    game = db_session.scalar(select(Game).where(Game.game_pk == 778001))
    assert game is not None

    prediction = generate_prediction_for_game(db_session, game)

    assert prediction.id is not None
    assert prediction.home_win_proba == pytest.approx(0.62)
    assert prediction.away_win_proba == pytest.approx(0.38)
    assert prediction.features == MOCK_PREDICTION["features"]
    assert prediction.notes == "using probable starters"
    assert prediction.model_version.run_id == RUN_ID
    assert prediction.game.game_pk == 778001


@patch(
    "baseball_backend.services.prediction_service.predict_game",
    return_value=MOCK_PREDICTION,
)
def test_generate_prediction_for_game_is_idempotent(
    _mock_predict: object,
    db_session: Session,
) -> None:
    game = db_session.scalar(select(Game).where(Game.game_pk == 778001))
    assert game is not None

    first = generate_prediction_for_game(db_session, game)
    second = generate_prediction_for_game(db_session, game)

    assert first.id == second.id
    assert len(db_session.scalars(select(Prediction)).all()) == 1


@patch(
    "baseball_backend.services.prediction_service.predict_game",
    return_value=MOCK_PREDICTION,
)
def test_generate_missing_predictions_for_date(
    _mock_predict: object,
    db_session: Session,
) -> None:
    created = generate_missing_predictions_for_date(db_session, "2025-04-06")

    assert created == 1
    prediction = db_session.scalar(select(Prediction))
    assert prediction is not None
    assert prediction.home_win_proba == pytest.approx(0.62)


@patch(
    "baseball_backend.services.prediction_service.predict_game",
    return_value=MOCK_PREDICTION,
)
def test_generate_missing_predictions_skips_final_games(
    _mock_predict: object,
    db_session: Session,
) -> None:
    game = db_session.scalar(select(Game).where(Game.game_pk == 778001))
    assert game is not None
    game.detailed_state = "Final"
    db_session.commit()

    created = generate_missing_predictions_for_date(db_session, "2025-04-06")

    assert created == 0
    assert db_session.scalar(select(Prediction)) is None


def test_get_prediction_for_game_pk_raises_when_game_missing(db_session: Session) -> None:
    with pytest.raises(GameNotFoundError, match="Game not found"):
        get_prediction_for_game_pk(db_session, 999999)


def test_get_prediction_for_game_pk_returns_none_without_active_model(
    tmp_path: Path,
) -> None:
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
        for pitcher_data in SAMPLE_PITCHERS:
            session.add(Player(**pitcher_data))
        session.add(_make_sample_game())
        session.commit()

        assert get_prediction_for_game_pk(session, 778001) is None
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=tables)
        engine.dispose()
        for column in jsonb_columns:
            column.type = JSONB()


@pytest.fixture
def api_client(db_session: Session) -> Generator[TestClient, None, None]:
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
def auth_headers(api_client: TestClient) -> dict[str, str]:
    api_client.post("/auth/register", json={"email": "fan@example.com", "password": "secretpass"})
    login = api_client.post(
        "/auth/login",
        data={"username": "fan@example.com", "password": "secretpass"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def api_client_no_model(tmp_path: Path) -> Generator[TestClient, None, None]:
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
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        for team_data in SAMPLE_TEAMS:
            session.add(Team(**team_data))
        for pitcher_data in SAMPLE_PITCHERS:
            session.add(Player(**pitcher_data))
        session.add(_make_sample_game())
        session.commit()

        def override_get_db() -> Generator[Session, None, None]:
            try:
                yield session
            finally:
                pass

        app.dependency_overrides[get_db] = override_get_db
        with TestClient(app) as test_client:
            yield test_client
        app.dependency_overrides.clear()
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=tables)
        engine.dispose()
        for column in jsonb_columns:
            column.type = JSONB()


@pytest.fixture
def auth_headers_no_model(api_client_no_model: TestClient) -> dict[str, str]:
    api_client_no_model.post(
        "/auth/register",
        json={"email": "fan@example.com", "password": "secretpass"},
    )
    login = api_client_no_model.post(
        "/auth/login",
        data={"username": "fan@example.com", "password": "secretpass"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_get_prediction_returns_null_without_active_model(
    api_client_no_model: TestClient,
    auth_headers_no_model: dict[str, str],
) -> None:
    response = api_client_no_model.get("/predictions/778001", headers=auth_headers_no_model)
    assert response.status_code == 200
    assert response.json() is None


@patch(
    "baseball_backend.services.prediction_service.predict_game",
    return_value=MOCK_PREDICTION,
)
def test_get_prediction_returns_prediction(
    _mock_predict: object,
    api_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = api_client.get("/predictions/778001", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body is not None
    assert body["game_pk"] == 778001
    assert body["home_win_proba"] == pytest.approx(0.62)
    assert body["away_win_proba"] == pytest.approx(0.38)
    assert body["model_version"]["run_id"] == RUN_ID
