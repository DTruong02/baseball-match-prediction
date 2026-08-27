"""Tests for versioned training artifacts and evaluation metrics."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from baseball_analyze.features import FEATURE_COLUMNS
from baseball_analyze.models.artifacts import (
    MANIFEST_FILENAME,
    METRICS_FILENAME,
    MODEL_FILENAME,
    build_manifest,
    make_run_id,
    save_versioned_run,
)
from baseball_analyze.models.model import evaluate, load_artifact


def _tiny_fitted_pipeline() -> Pipeline:
    X = np.array([[0.0], [1.0], [0.1], [0.9]])
    y = np.array([0, 1, 0, 1])
    pipe = Pipeline([("scale", StandardScaler()), ("clf", LogisticRegression(max_iter=200))])
    pipe.fit(X, y)
    return pipe


def test_evaluate_includes_roc_auc_and_core_metrics():
    y_true = np.array([0, 1, 0, 1, 1, 0])
    proba = np.array([0.1, 0.9, 0.2, 0.8, 0.7, 0.3])
    metrics = evaluate(y_true, proba)

    assert set(metrics) == {"accuracy", "roc_auc", "log_loss", "brier"}
    assert 0.0 <= metrics["accuracy"] <= 1.0
    assert 0.0 <= metrics["roc_auc"] <= 1.0
    assert metrics["log_loss"] >= 0.0
    assert 0.0 <= metrics["brier"] <= 1.0


def test_evaluate_roc_auc_nan_for_single_class():
    y_true = np.array([1, 1, 1])
    proba = np.array([0.6, 0.7, 0.8])
    metrics = evaluate(y_true, proba)
    assert math.isnan(metrics["roc_auc"])


def test_make_run_id_format():
    when = datetime(2026, 8, 24, 20, 8, 12, tzinfo=timezone.utc)
    run_id = make_run_id(when=when)
    assert run_id.startswith("20260824T200812Z_")
    assert len(run_id.split("_", 1)[1]) == 8


def test_build_manifest_shape():
    created = datetime(2026, 8, 24, 20, 8, 12, tzinfo=timezone.utc)
    manifest = build_manifest(
        run_id="20260824T200812Z_deadbeef",
        seasons=[2022, 2023],
        val_seasons=[2023],
        split_type="time",
        train_rows=100,
        val_rows=40,
        max_games=200,
        test_size=0.25,
        hyperparameters={"calibrate": False, "class_weight": "balanced", "c_grid": [1.0], "best_C": 1.0},
        created_at=created,
        git_hash="abc123",
    )

    assert manifest["run_id"] == "20260824T200812Z_deadbeef"
    assert manifest["created_at"] == "2026-08-24T20:08:12+00:00"
    assert manifest["feature_columns"] == FEATURE_COLUMNS
    assert manifest["seasons"] == [2022, 2023]
    assert manifest["val_seasons"] == [2023]
    assert manifest["git_hash"] == "abc123"
    assert manifest["hyperparameters"]["best_C"] == 1.0


def test_save_versioned_run_writes_files_and_convenience_copy(tmp_path: Path):
    model = _tiny_fitted_pipeline()
    metrics = {"accuracy": 0.5, "roc_auc": float("nan"), "log_loss": 0.7, "brier": 0.25}
    run_id = "20260824T200812Z_testrun1"
    manifest = build_manifest(
        run_id=run_id,
        seasons=[2023],
        val_seasons=[],
        split_type="random",
        train_rows=10,
        val_rows=4,
        max_games=None,
        test_size=0.25,
        hyperparameters={"calibrate": False, "class_weight": "balanced", "c_grid": [1.0], "best_C": 1.0},
        git_hash=None,
    )

    artifacts_root = tmp_path / "artifacts"
    convenience = artifacts_root / "model.joblib"
    run_dir = save_versioned_run(
        model=model,
        metrics=metrics,
        manifest=manifest,
        artifacts_root=artifacts_root,
        convenience_out=convenience,
        run_id=run_id,
    )

    assert run_dir == artifacts_root / run_id
    assert (run_dir / MODEL_FILENAME).is_file()
    assert (run_dir / METRICS_FILENAME).is_file()
    assert (run_dir / MANIFEST_FILENAME).is_file()
    assert convenience.is_file()

    metrics_payload = json.loads((run_dir / METRICS_FILENAME).read_text(encoding="utf-8"))
    assert metrics_payload["accuracy"] == 0.5
    assert metrics_payload["roc_auc"] is None  # NaN -> null
    assert metrics_payload["log_loss"] == 0.7
    assert metrics_payload["brier"] == 0.25

    manifest_payload = json.loads((run_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest_payload["run_id"] == run_id
    assert "git_hash" not in manifest_payload
    assert manifest_payload["feature_columns"] == FEATURE_COLUMNS

    loaded_model, cols = load_artifact(convenience)
    assert cols == FEATURE_COLUMNS
    assert loaded_model is not None
