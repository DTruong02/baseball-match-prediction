"""Tests for live WP inference gating and persistence."""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import JSON, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from baseball_analyze.features.in_game import IN_GAME_FEATURE_COLUMNS
from baseball_analyze.models.artifacts import build_manifest, save_versioned_run
from baseball_backend.db.base import Base
from baseball_backend.db.models import (
    Game,
    GameEvent,
    ModelVersion,
    ModelVersionKind,
    ModelVersionStatus,
    Prediction,
    Team,
)
from baseball_backend.services.live_normalize import GameLiveState, NormalizedGameEvent
from baseball_backend.services.live_prediction_service import (
    is_meaningful_event,
    should_run_live_inference,
)
from baseball_backend.services.model_registry import register_model_from_run
from baseball_backend.services.live_ingestion import sync_live_game

LIVE_FEED = json.loads(
    (Path(__file__).parent / "fixtures" / "live_feed_sample.json").read_text(
        encoding="utf-8"
    )
)
RUN_ID = "20260906T120000Z_ingameaa"


def _tiny_pipeline() -> Pipeline:
    n = len(IN_GAME_FEATURE_COLUMNS)
    X = np.vstack([np.zeros(n), np.ones(n), np.full(n, 0.2), np.full(n, 0.8)])
    y = np.array([0, 1, 0, 1])
    pipe = Pipeline([("scale", StandardScaler()), ("clf", LogisticRegression(max_iter=200))])
    pipe.fit(X, y)
    return pipe


def _write_in_game_run(artifacts_root: Path, run_id: str) -> Path:
    metrics = {"accuracy": 0.55, "roc_auc": 0.58, "log_loss": 0.68, "brier": 0.24}
    manifest = build_manifest(
        run_id=run_id,
        seasons=[2024, 2025],
        val_seasons=[2025],
        split_type="time",
        train_rows=100,
        val_rows=40,
        max_games=50,
        test_size=0.25,
        hyperparameters={"calibrate": False, "best_C": 1.0},
        feature_columns=IN_GAME_FEATURE_COLUMNS,
        kind="in_game",
        git_hash="deadbeef",
    )
    return save_versioned_run(
        model=_tiny_pipeline(),
        metrics=metrics,
        manifest=manifest,
        artifacts_root=artifacts_root,
        convenience_out=artifacts_root / "in_game_model.joblib",
        run_id=run_id,
    )


@pytest.fixture
def db_session(tmp_path: Path) -> Generator[Session, None, None]:
    artifacts_root = tmp_path / "artifacts"
    _write_in_game_run(artifacts_root, RUN_ID)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    jsonb_columns: list = []
    for table in (GameEvent.__table__, ModelVersion.__table__, Prediction.__table__):
        for column in table.columns:
            if isinstance(column.type, JSONB):
                jsonb_columns.append(column)
                column.type = JSON()
    tables = [
        Team.__table__,
        Game.__table__,
        GameEvent.__table__,
        ModelVersion.__table__,
        Prediction.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        session.add_all(
            [
                Team(id=147, abbreviation="NYY", name="Yankees", city="New York"),
                Team(id=111, abbreviation="BOS", name="Red Sox", city="Boston"),
            ]
        )
        session.commit()
        register_model_from_run(
            session,
            RUN_ID,
            artifacts_root=artifacts_root,
            kind=ModelVersionKind.IN_GAME.value,
            activate=True,
        )
        yield session
    finally:
        session.close()
        for column in jsonb_columns:
            column.type = JSONB()


def _seed_game(db: Session) -> Game:
    game = Game(
        game_pk=824239,
        game_date=date(2026, 8, 15),
        season=2026,
        status="Live",
        detailed_state="In Progress",
        home_team_id=147,
        away_team_id=111,
    )
    db.add(game)
    db.commit()
    return game


def test_is_meaningful_event_scoring_and_outs() -> None:
    assert is_meaningful_event({"is_scoring_play": True, "event_type": "home_run"})
    assert is_meaningful_event({"rbi": 1, "event_type": "single"})
    assert is_meaningful_event({"event_type": "strikeout", "is_complete": True})
    assert is_meaningful_event({"event_type": "pitching_substitution"})
    assert not is_meaningful_event(
        {"event_type": "single", "is_scoring_play": False, "rbi": 0, "is_complete": True}
    )


def test_should_run_on_first_prediction_and_state_deltas() -> None:
    state = GameLiveState(
        home_score=1,
        away_score=0,
        status="Live",
        detailed_state="In Progress",
        current_inning=3,
        inning_state="Top",
        is_top_inning=True,
        outs=1,
        balls=0,
        strikes=0,
    )
    assert should_run_live_inference(
        previous_state=None,
        new_state=state,
        new_events=[],
        previous_pitcher_id=None,
        current_pitcher_id=10,
        has_existing_prediction=False,
    )
    prev = GameLiveState(
        home_score=0,
        away_score=0,
        status="Live",
        detailed_state="In Progress",
        current_inning=3,
        inning_state="Top",
        is_top_inning=True,
        outs=0,
        balls=0,
        strikes=0,
    )
    assert should_run_live_inference(
        previous_state=prev,
        new_state=state,
        new_events=[],
        previous_pitcher_id=10,
        current_pitcher_id=10,
        has_existing_prediction=True,
    )
    assert should_run_live_inference(
        previous_state=state,
        new_state=state,
        new_events=[],
        previous_pitcher_id=10,
        current_pitcher_id=11,
        has_existing_prediction=True,
    )
    assert not should_run_live_inference(
        previous_state=state,
        new_state=state,
        new_events=[],
        previous_pitcher_id=10,
        current_pitcher_id=10,
        has_existing_prediction=True,
    )
    scoring = NormalizedGameEvent(
        event_id="play-9",
        type="play",
        sequence=10,
        payload={"is_scoring_play": True, "event_type": "home_run"},
    )
    assert should_run_live_inference(
        previous_state=state,
        new_state=state,
        new_events=[scoring],
        previous_pitcher_id=10,
        current_pitcher_id=10,
        has_existing_prediction=True,
    )


@patch("baseball_backend.services.live_ingestion.fetch_live_feed", return_value=LIVE_FEED)
@patch("baseball_backend.services.live_ingestion.cache_live_state")
@patch(
    "baseball_analyze.features.in_game.fetch_pitcher_season_kbb9",
    return_value=2.5,
)
@patch(
    "baseball_analyze.features.in_game.fetch_pitcher_season_fip_xfip",
    return_value=(3.8, 3.9),
)
@patch(
    "baseball_analyze.features.in_game.bullpen_fip_by_team",
    return_value={"NYY": 4.0, "BOS": 4.1},
)
@patch(
    "baseball_analyze.features.in_game.mlb_abbrev_to_fangraphs",
    side_effect=lambda abbrev, _season: abbrev,
)
def test_sync_live_game_persists_and_pushes_live_wp(
    _map,
    _bull,
    _fip,
    _kbb9,
    mock_cache,
    _feed,
    db_session: Session,
) -> None:
    _seed_game(db_session)

    summary = sync_live_game(db_session, 824239, retries=0)

    assert summary["live_wp_updated"] is True
    assert "home_win_proba" in summary
    assert "away_win_proba" in summary

    game = db_session.scalar(select(Game).where(Game.game_pk == 824239))
    assert game is not None
    assert game.live_state is not None
    assert game.live_state["home_win_proba"] == pytest.approx(summary["home_win_proba"])

    preds = db_session.scalars(select(Prediction)).all()
    assert len(preds) == 1
    assert preds[0].home_win_proba is not None

    model = db_session.scalar(
        select(ModelVersion).where(ModelVersion.status == ModelVersionStatus.ACTIVE.value)
    )
    assert model is not None
    assert model.kind == ModelVersionKind.IN_GAME.value

    mock_cache.assert_called_once()
    _args, kwargs = mock_cache.call_args
    assert kwargs["home_win_proba"] == pytest.approx(summary["home_win_proba"])
    assert kwargs["away_win_proba"] == pytest.approx(summary["away_win_proba"])

    # Second sync with no new events should not re-infer.
    summary2 = sync_live_game(db_session, 824239, retries=0)
    assert summary2["events_inserted"] == 0
    assert summary2["live_wp_updated"] is False
    assert summary2["home_win_proba"] == pytest.approx(summary["home_win_proba"])


@patch("baseball_backend.services.live_ingestion.fetch_live_feed", return_value=LIVE_FEED)
@patch("baseball_backend.services.live_ingestion.cache_live_state")
@patch(
    "baseball_analyze.features.in_game.fetch_pitcher_season_kbb9",
    return_value=2.5,
)
@patch(
    "baseball_analyze.features.in_game.fetch_pitcher_season_fip_xfip",
    return_value=(3.8, 3.9),
)
@patch(
    "baseball_analyze.features.in_game.bullpen_fip_by_team",
    return_value={"NYY": 4.0, "BOS": 4.1},
)
@patch(
    "baseball_analyze.features.in_game.mlb_abbrev_to_fangraphs",
    side_effect=lambda abbrev, _season: abbrev,
)
@patch(
    "baseball_backend.services.live_prediction_service.predict_in_game",
)
def test_sync_live_game_attaches_wp_swing_explanation(
    mock_predict,
    _map,
    _bull,
    _fip,
    _kbb9,
    mock_cache,
    _feed,
    db_session: Session,
) -> None:
    game = _seed_game(db_session)
    # Prior live snapshot with WP so the next inference can compute a swing.
    game.live_state = {
        "game_pk": 824239,
        "home_score": 0,
        "away_score": 2,
        "status": "Live",
        "detailed_state": "In Progress",
        "current_inning": 3,
        "inning_state": "Top",
        "is_top_inning": True,
        "outs": 1,
        "balls": 0,
        "strikes": 0,
        "events_inserted": 0,
        "home_win_proba": 0.40,
        "away_win_proba": 0.60,
        "updated_at": "2026-08-15T18:00:00+00:00",
    }
    db_session.commit()

    mock_predict.return_value = {
        "home_win_proba": 0.58,
        "away_win_proba": 0.42,
        "features": {},
        "model_version": RUN_ID,
        "notes": [],
    }

    summary = sync_live_game(db_session, 824239, retries=0)

    assert summary["live_wp_updated"] is True
    assert "wp_explanation" in summary
    assert "WP +" in summary["wp_explanation"]
    assert summary["wp_delta_home"] == pytest.approx(0.18)

    game = db_session.scalar(select(Game).where(Game.game_pk == 824239))
    assert game is not None
    assert game.live_state is not None
    assert game.live_state["wp_explanation"] == summary["wp_explanation"]

    _args, kwargs = mock_cache.call_args
    assert kwargs["wp_explanation"] == summary["wp_explanation"]
    assert kwargs["wp_delta_home"] == pytest.approx(0.18)
