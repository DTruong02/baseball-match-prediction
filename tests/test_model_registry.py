"""Tests for model registry service."""

from __future__ import annotations

import shutil
from collections.abc import Generator
from pathlib import Path

import numpy as np
import pytest
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
from baseball_backend.db.models import ModelVersion, ModelVersionKind, ModelVersionStatus
from baseball_backend.services.model_registry import (
    ArtifactError,
    ModelVersionNotFoundError,
    activate_model_version,
    get_active_pregame_model,
    register_model_from_run,
)

RUN_ID_A = "20260824T200812Z_runaaaaa"
RUN_ID_B = "20260824T200813Z_runbbbbb"


def _tiny_fitted_pipeline() -> Pipeline:
    X = np.array([[0.0], [1.0], [0.1], [0.9]])
    y = np.array([0, 1, 0, 1])
    pipe = Pipeline([("scale", StandardScaler()), ("clf", LogisticRegression(max_iter=200))])
    pipe.fit(X, y)
    return pipe


def _write_run(
    artifacts_root: Path,
    run_id: str,
    *,
    seasons: list[int] | None = None,
    git_hash: str | None = "abc123",
) -> Path:
    metrics = {"accuracy": 0.55, "roc_auc": 0.58, "log_loss": 0.68, "brier": 0.24}
    manifest = build_manifest(
        run_id=run_id,
        seasons=seasons or [2023, 2024],
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
        git_hash=git_hash,
    )
    convenience = artifacts_root / "model.joblib"
    return save_versioned_run(
        model=_tiny_fitted_pipeline(),
        metrics=metrics,
        manifest=manifest,
        artifacts_root=artifacts_root,
        convenience_out=convenience,
        run_id=run_id,
    )


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    jsonb_columns: list = []
    for column in ModelVersion.__table__.columns:
        if isinstance(column.type, JSONB):
            jsonb_columns.append(column)
            column.type = JSON()
    try:
        Base.metadata.create_all(bind=engine, tables=[ModelVersion.__table__])
        session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
        try:
            yield session
        finally:
            session.close()
    finally:
        for column in jsonb_columns:
            column.type = JSONB()


@pytest.fixture
def artifacts_root(tmp_path: Path) -> Path:
    root = tmp_path / "artifacts"
    _write_run(root, RUN_ID_A)
    _write_run(root, RUN_ID_B, seasons=[2024], git_hash=None)
    return root


def test_register_model_from_run_inserts_row(
    db_session: Session,
    artifacts_root: Path,
) -> None:
    model_version = register_model_from_run(
        db_session,
        RUN_ID_A,
        artifacts_root=artifacts_root,
    )

    assert model_version.id is not None
    assert model_version.run_id == RUN_ID_A
    assert model_version.kind == ModelVersionKind.PREGAME.value
    assert model_version.status == ModelVersionStatus.ARCHIVED.value
    assert model_version.metrics == {
        "accuracy": 0.55,
        "roc_auc": 0.58,
        "log_loss": 0.68,
        "brier": 0.24,
    }
    assert model_version.feature_columns == FEATURE_COLUMNS
    assert model_version.train_seasons == [2023, 2024]
    assert model_version.git_hash == "abc123"
    assert model_version.hyperparameters is not None
    assert model_version.hyperparameters["best_C"] == 1.0
    assert Path(model_version.artifact_path).name == "model.joblib"
    assert Path(model_version.artifact_path).parent.name == RUN_ID_A


def test_register_model_from_run_is_idempotent(
    db_session: Session,
    artifacts_root: Path,
) -> None:
    first = register_model_from_run(db_session, RUN_ID_A, artifacts_root=artifacts_root)
    second = register_model_from_run(db_session, RUN_ID_A, artifacts_root=artifacts_root)

    assert first.id == second.id
    assert db_session.scalar(select(ModelVersion).where(ModelVersion.run_id == RUN_ID_A)) is not None
    assert len(db_session.scalars(select(ModelVersion)).all()) == 1


def test_register_model_activate_archives_previous_active(
    db_session: Session,
    artifacts_root: Path,
) -> None:
    first = register_model_from_run(
        db_session,
        RUN_ID_A,
        artifacts_root=artifacts_root,
        activate=True,
    )
    second = register_model_from_run(
        db_session,
        RUN_ID_B,
        artifacts_root=artifacts_root,
        activate=True,
    )

    db_session.refresh(first)
    assert first.status == ModelVersionStatus.ARCHIVED.value
    assert second.status == ModelVersionStatus.ACTIVE.value
    assert get_active_pregame_model(db_session).run_id == RUN_ID_B


def test_activate_model_version(
    db_session: Session,
    artifacts_root: Path,
) -> None:
    first = register_model_from_run(db_session, RUN_ID_A, artifacts_root=artifacts_root)
    second = register_model_from_run(db_session, RUN_ID_B, artifacts_root=artifacts_root)

    activate_model_version(db_session, first)

    db_session.refresh(first)
    db_session.refresh(second)
    assert first.status == ModelVersionStatus.ACTIVE.value
    assert second.status == ModelVersionStatus.ARCHIVED.value
    assert get_active_pregame_model(db_session).run_id == RUN_ID_A


def test_get_active_pregame_model_raises_when_none(db_session: Session) -> None:
    with pytest.raises(ModelVersionNotFoundError, match="No active pregame"):
        get_active_pregame_model(db_session)


def test_get_active_pregame_model_raises_when_multiple_active(
    db_session: Session,
    artifacts_root: Path,
) -> None:
    first = register_model_from_run(
        db_session,
        RUN_ID_A,
        artifacts_root=artifacts_root,
        activate=True,
    )
    second = register_model_from_run(db_session, RUN_ID_B, artifacts_root=artifacts_root)
    second.status = ModelVersionStatus.ACTIVE.value
    db_session.commit()

    with pytest.raises(ModelVersionNotFoundError, match="Multiple active pregame"):
        get_active_pregame_model(db_session)

    assert first.status == ModelVersionStatus.ACTIVE.value


def test_register_model_missing_run_dir_raises(
    db_session: Session,
    artifacts_root: Path,
) -> None:
    with pytest.raises(ArtifactError, match="Run directory not found"):
        register_model_from_run(
            db_session,
            "missing-run-id",
            artifacts_root=artifacts_root,
        )


def test_register_model_manifest_run_id_mismatch_raises(
    db_session: Session,
    tmp_path: Path,
) -> None:
    artifacts_root = tmp_path / "artifacts"
    _write_run(artifacts_root, RUN_ID_A)
    mismatch_dir = artifacts_root / "wrong-run-id"
    mismatch_dir.mkdir()
    shutil.copy2(artifacts_root / RUN_ID_A / "model.joblib", mismatch_dir / "model.joblib")
    for filename in ("metrics.json", "manifest.json"):
        source = artifacts_root / RUN_ID_A / filename
        (mismatch_dir / filename).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(ArtifactError, match="does not match"):
        register_model_from_run(
            db_session,
            "wrong-run-id",
            artifacts_root=artifacts_root,
        )
