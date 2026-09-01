"""Compute production performance metrics for stored predictions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from baseball_analyze.models.model import evaluate
from baseball_backend.db.models import Game, ModelVersion, Prediction
from baseball_backend.services.model_registry import (
    ModelVersionNotFoundError,
    get_active_pregame_model,
)

_FINAL_STATES = frozenset({"Final", "Game Over", "Completed Early"})
N_CALIBRATION_BUCKETS = 10


def _json_safe_metric(value: float) -> float | None:
    if value != value:  # NaN
        return None
    return float(value)


def _is_scored_game(game: Game) -> bool:
    if game.detailed_state not in _FINAL_STATES:
        return False
    if game.home_score is None or game.away_score is None:
        return False
    return game.home_score != game.away_score


def _home_team_won(game: Game) -> bool:
    assert game.home_score is not None and game.away_score is not None
    return game.home_score > game.away_score


def compute_calibration_buckets(
    y_true: np.ndarray,
    proba_home: np.ndarray,
    *,
    n_buckets: int = N_CALIBRATION_BUCKETS,
) -> list[dict[str, Any]]:
    buckets: list[dict[str, Any]] = []
    for i in range(n_buckets):
        low = i / n_buckets
        high = (i + 1) / n_buckets
        if i < n_buckets - 1:
            mask = (proba_home >= low) & (proba_home < high)
        else:
            mask = (proba_home >= low) & (proba_home <= high)
        n = int(mask.sum())
        if n > 0:
            buckets.append(
                {
                    "bin_low": low,
                    "bin_high": high,
                    "n": n,
                    "predicted_mean": float(proba_home[mask].mean()),
                    "actual_rate": float(y_true[mask].mean()),
                }
            )
        else:
            buckets.append(
                {
                    "bin_low": low,
                    "bin_high": high,
                    "n": 0,
                    "predicted_mean": None,
                    "actual_rate": None,
                }
            )
    return buckets


def _load_scored_pairs(
    db: Session,
    model_version_id: int,
    *,
    season: int | None = None,
    team_abbrev: str | None = None,
    confidence_min: float | None = None,
    confidence_max: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    predictions = db.scalars(
        select(Prediction)
        .options(
            joinedload(Prediction.game).joinedload(Game.home_team),
            joinedload(Prediction.game).joinedload(Game.away_team),
        )
        .where(Prediction.model_version_id == model_version_id)
    ).all()

    y_true_list: list[int] = []
    proba_list: list[float] = []

    for prediction in predictions:
        game = prediction.game
        if not _is_scored_game(game):
            continue
        if season is not None and game.season != season:
            continue
        if team_abbrev is not None:
            home_abbr = game.home_team.abbreviation
            away_abbr = game.away_team.abbreviation
            if team_abbrev not in (home_abbr, away_abbr):
                continue
        if prediction.home_win_proba is None:
            continue

        confidence = max(prediction.home_win_proba, prediction.away_win_proba or 0.0)
        if confidence_min is not None and confidence < confidence_min:
            continue
        if confidence_max is not None and confidence > confidence_max:
            continue

        y_true_list.append(1 if _home_team_won(game) else 0)
        proba_list.append(float(prediction.home_win_proba))

    if not y_true_list:
        return np.array([], dtype=int), np.array([], dtype=float)
    return np.array(y_true_list, dtype=int), np.array(proba_list, dtype=float)


def compute_model_performance(
    db: Session,
    *,
    model_version_id: int | None = None,
    season: int | None = None,
    team_abbrev: str | None = None,
    confidence_min: float | None = None,
    confidence_max: float | None = None,
) -> dict[str, Any]:
    """Join predictions to final game outcomes and compute aggregate metrics."""
    if model_version_id is None:
        model_version = get_active_pregame_model(db)
    else:
        model_version = db.get(ModelVersion, model_version_id)
        if model_version is None:
            raise ModelVersionNotFoundError(
                f"Model version not found: id={model_version_id}"
            )

    y_true, proba_home = _load_scored_pairs(
        db,
        model_version.id,
        season=season,
        team_abbrev=team_abbrev,
        confidence_min=confidence_min,
        confidence_max=confidence_max,
    )
    n_games = int(len(y_true))

    if n_games == 0:
        return {
            "model_version_id": model_version.id,
            "run_id": model_version.run_id,
            "n_games": 0,
            "accuracy": None,
            "roc_auc": None,
            "log_loss": None,
            "brier": None,
            "calibration_buckets": [],
        }

    metrics = evaluate(y_true, proba_home)
    return {
        "model_version_id": model_version.id,
        "run_id": model_version.run_id,
        "n_games": n_games,
        "accuracy": _json_safe_metric(metrics["accuracy"]),
        "roc_auc": _json_safe_metric(metrics["roc_auc"]),
        "log_loss": _json_safe_metric(metrics["log_loss"]),
        "brier": _json_safe_metric(metrics["brier"]),
        "calibration_buckets": compute_calibration_buckets(y_true, proba_home),
    }


def score_and_store_active_model(db: Session) -> ModelVersion:
    """
    Compute production metrics for the active pregame model and persist them.

    Returns the updated ``ModelVersion`` row.
    """
    model_version = get_active_pregame_model(db)
    performance = compute_model_performance(db, model_version_id=model_version.id)
    model_version.production_metrics = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "n_games": performance["n_games"],
        "accuracy": performance["accuracy"],
        "roc_auc": performance["roc_auc"],
        "log_loss": performance["log_loss"],
        "brier": performance["brier"],
        "calibration_buckets": performance["calibration_buckets"],
    }
    db.commit()
    db.refresh(model_version)
    return model_version
