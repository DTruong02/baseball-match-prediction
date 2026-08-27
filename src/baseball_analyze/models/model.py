"""Scikit-learn pipeline for P(home win)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from baseball_analyze.features import FEATURE_COLUMNS

Artifact = tuple[Pipeline, list[str]]


def build_pipeline(
    calibrate: bool = False,
    *,
    C: float = 1.0,
    class_weight: str | None = "balanced",
) -> Pipeline:
    base = LogisticRegression(
        max_iter=2000,
        C=C,
        class_weight=class_weight,
        random_state=42,
    )
    clf: Any = Pipeline(
        [
            ("scale", StandardScaler()),
            ("clf", base),
        ]
    )
    if calibrate:
        clf = CalibratedClassifierCV(clf, method="isotonic", cv=3)
    return clf


def train_pipeline(
    X: np.ndarray,
    y: np.ndarray,
    calibrate: bool = False,
    *,
    C: float = 1.0,
    class_weight: str | None = "balanced",
) -> Pipeline:
    model = build_pipeline(calibrate=calibrate, C=C, class_weight=class_weight)
    model.fit(X, y)
    return model


def evaluate(y_true: np.ndarray, proba_home: np.ndarray) -> dict[str, float]:
    y_hat = (proba_home >= 0.5).astype(int)
    if len(np.unique(y_true)) < 2:
        roc_auc = float("nan")
    else:
        roc_auc = float(roc_auc_score(y_true, proba_home))
    return {
        "accuracy": float((y_hat == y_true).mean()),
        "roc_auc": roc_auc,
        # Binary home-win labels; pass explicitly so single-class folds still score.
        "log_loss": float(log_loss(y_true, proba_home, labels=[0, 1])),
        "brier": float(brier_score_loss(y_true, proba_home)),
    }


def predict_home_win_proba(model: Pipeline, X: np.ndarray) -> np.ndarray:
    """Return shape (n_samples,) with P(home team wins). Binary clf column 1 = positive class."""
    proba = model.predict_proba(X)
    return proba[:, 1]


def save_artifact(model: Pipeline, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: Artifact = (model, FEATURE_COLUMNS)
    joblib.dump(payload, path)


def load_artifact(path: Path) -> Artifact:
    payload = joblib.load(path)
    model, cols = payload
    if cols != FEATURE_COLUMNS:
        raise ValueError(f"Feature mismatch: artifact {cols} vs code {FEATURE_COLUMNS}")
    return model, cols
