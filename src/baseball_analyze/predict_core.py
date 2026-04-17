"""Shared prediction helpers for CLI and chat tooling."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from baseball_analyze.features import FeatureRow, build_features_for_game, feature_vector
from baseball_analyze.mlb_client import ScheduledGame
from baseball_analyze.model import load_artifact, predict_home_win_proba


def build_feature_rows(
    games: list[ScheduledGame],
    *,
    cache_dir: Path | None = None,
) -> tuple[list[FeatureRow], list[tuple[int, list[str]]]]:
    rows: list[FeatureRow] = []
    notes_all: list[tuple[int, list[str]]] = []
    for g in games:
        if g.detailed_state in ("Postponed", "Cancelled"):
            continue
        fr = build_features_for_game(g, cache_dir=cache_dir)
        rows.append(fr)
        if fr.notes:
            notes_all.append((g.game_pk, fr.notes))
    return rows, notes_all


def predict_for_feature_rows(
    *,
    model_path: Path,
    rows: list[FeatureRow],
) -> np.ndarray:
    model, _cols = load_artifact(model_path)
    X = np.vstack([feature_vector(r) for r in rows])
    return predict_home_win_proba(model, X)

