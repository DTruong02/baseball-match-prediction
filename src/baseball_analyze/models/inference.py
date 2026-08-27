"""Clean inference API for pregame home-win predictions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from baseball_analyze.data.mlb_client import fetch_schedule_by_game_pk
from baseball_analyze.features import FEATURE_COLUMNS, build_features_for_game, feature_vector
from baseball_analyze.models.artifacts import MANIFEST_FILENAME, METRICS_FILENAME
from baseball_analyze.models.model import load_artifact, predict_home_win_proba


def resolve_model_version(model_path: Path) -> str:
    """
    Best-effort model version string for a joblib artifact path.

    Prefers ``run_id`` from a sibling ``manifest.json`` (versioned run dirs),
    then the parent directory name when it looks like a run folder, else the
    resolved model path.
    """
    path = Path(model_path)
    manifest_path = path.parent / MANIFEST_FILENAME
    if manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            data = None
        if isinstance(data, dict):
            run_id = data.get("run_id")
            if run_id:
                return str(run_id)

    parent = path.parent
    if (parent / METRICS_FILENAME).is_file() and parent.name not in {"", ".", "artifacts"}:
        return parent.name

    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def predict_game(
    game_pk: int,
    model_path: str | Path,
    *,
    cache_dir: Path | str | None = None,
) -> dict[str, Any]:
    """
    Predict home/away win probabilities for a single MLB ``gamePk``.

    Returns a dict with:
    ``home_win_proba``, ``away_win_proba``, ``features``, ``model_version``,
    ``notes``, plus identity fields (``game_pk``, ``season``, ``home_fg``,
    ``away_fg``).
    """
    path = Path(model_path)
    cache = Path(cache_dir) if cache_dir is not None else None

    game = fetch_schedule_by_game_pk(int(game_pk))
    if game is None:
        raise ValueError(f"Could not load schedule for gamePk={game_pk}")
    if game.detailed_state in ("Postponed", "Cancelled"):
        raise ValueError(f"Game {game_pk} is {game.detailed_state}")

    model, _cols = load_artifact(path)
    fr = build_features_for_game(game, cache_dir=cache)
    X = np.vstack([feature_vector(fr)])
    p_home = float(predict_home_win_proba(model, X)[0])

    return {
        "game_pk": fr.game_pk,
        "season": fr.season,
        "home_fg": fr.home_fg,
        "away_fg": fr.away_fg,
        "home_win_proba": p_home,
        "away_win_proba": 1.0 - p_home,
        "features": {c: float(fr.features[c]) for c in FEATURE_COLUMNS},
        "model_version": resolve_model_version(path),
        "notes": list(fr.notes),
    }
