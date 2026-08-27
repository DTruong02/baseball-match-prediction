"""Versioned training run artifacts (model + metrics + manifest)."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sklearn.pipeline import Pipeline

from baseball_analyze.features import FEATURE_COLUMNS
from baseball_analyze.models.model import save_artifact

MODEL_FILENAME = "model.joblib"
METRICS_FILENAME = "metrics.json"
MANIFEST_FILENAME = "manifest.json"


def make_run_id(*, when: Optional[datetime] = None) -> str:
    """UTC timestamp run id with a short suffix to avoid same-second collisions."""
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}_{uuid.uuid4().hex[:8]}"


def optional_git_hash() -> Optional[str]:
    """Return current HEAD sha if git is available; otherwise None."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    hash_ = result.stdout.strip()
    return hash_ or None


def build_manifest(
    *,
    run_id: str,
    seasons: list[int],
    val_seasons: list[int],
    split_type: str,
    train_rows: int,
    val_rows: int,
    max_games: Optional[int],
    test_size: float,
    hyperparameters: dict[str, Any],
    feature_columns: Optional[list[str]] = None,
    created_at: Optional[datetime] = None,
    git_hash: Optional[str] = None,
) -> dict[str, Any]:
    """Build the JSON-serializable manifest for a training run."""
    created = created_at or datetime.now(timezone.utc)
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "created_at": created.isoformat(timespec="seconds"),
        "feature_columns": list(feature_columns if feature_columns is not None else FEATURE_COLUMNS),
        "seasons": list(seasons),
        "val_seasons": list(val_seasons),
        "split_type": split_type,
        "train_rows": int(train_rows),
        "val_rows": int(val_rows),
        "max_games": max_games,
        "test_size": float(test_size),
        "hyperparameters": hyperparameters,
    }
    if git_hash is not None:
        manifest["git_hash"] = git_hash
    return manifest


def _sanitize_for_json(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {k: _sanitize_for_json(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [_sanitize_for_json(v) for v in payload]
    if isinstance(payload, float) and (math.isnan(payload) or math.isinf(payload)):
        return None
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(_sanitize_for_json(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def save_versioned_run(
    *,
    model: Pipeline,
    metrics: dict[str, float],
    manifest: dict[str, Any],
    artifacts_root: Path,
    convenience_out: Path,
    run_id: Optional[str] = None,
) -> Path:
    """
    Write ``artifacts_root/<run_id>/{model.joblib,metrics.json,manifest.json}``
    and copy the model to ``convenience_out`` (CLI default path).

    Returns the run directory path.
    """
    rid = run_id or make_run_id()
    run_dir = artifacts_root / rid
    run_dir.mkdir(parents=True, exist_ok=False)

    model_path = run_dir / MODEL_FILENAME
    save_artifact(model, model_path)
    _write_json(run_dir / METRICS_FILENAME, {k: float(v) for k, v in metrics.items()})
    _write_json(run_dir / MANIFEST_FILENAME, manifest)

    convenience_out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(model_path, convenience_out)
    return run_dir
