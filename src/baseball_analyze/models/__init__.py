"""Model training, inference, and artifact management."""

from baseball_analyze.models.artifacts import (
    build_manifest,
    make_run_id,
    optional_git_hash,
    save_versioned_run,
)
from baseball_analyze.models.model import (
    evaluate,
    load_artifact,
    predict_home_win_proba,
    save_artifact,
    train_pipeline,
)

__all__ = [
    "build_manifest",
    "evaluate",
    "load_artifact",
    "make_run_id",
    "optional_git_hash",
    "predict_home_win_proba",
    "save_artifact",
    "save_versioned_run",
    "train_pipeline",
]
