"""Register and resolve trained ML model versions from Stage 1 artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from baseball_analyze.models.artifacts import (
    MANIFEST_FILENAME,
    METRICS_FILENAME,
    MODEL_FILENAME,
)
from baseball_backend.db.models import ModelVersion, ModelVersionKind, ModelVersionStatus


class ArtifactError(ValueError):
    """Raised when artifact files are missing or invalid."""


class ModelVersionNotFoundError(LookupError):
    """Raised when no active model version exists for a given kind."""


def _load_run_artifacts(run_dir: Path) -> tuple[dict, dict, Path]:
    """Validate run directory and return metrics, manifest, and model path."""
    if not run_dir.is_dir():
        raise ArtifactError(f"Run directory not found: {run_dir}")

    model_path = run_dir / MODEL_FILENAME
    metrics_path = run_dir / METRICS_FILENAME
    manifest_path = run_dir / MANIFEST_FILENAME

    missing = [
        name
        for name, path in [
            (MODEL_FILENAME, model_path),
            (METRICS_FILENAME, metrics_path),
            (MANIFEST_FILENAME, manifest_path),
        ]
        if not path.is_file()
    ]
    if missing:
        raise ArtifactError(f"Run directory {run_dir} is missing: {', '.join(missing)}")

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return metrics, manifest, model_path.resolve()


def _archive_active_for_kind(
    db: Session,
    kind: str,
    *,
    exclude_id: int | None = None,
) -> None:
    stmt = (
        update(ModelVersion)
        .where(
            ModelVersion.kind == kind,
            ModelVersion.status == ModelVersionStatus.ACTIVE.value,
        )
        .values(status=ModelVersionStatus.ARCHIVED.value)
    )
    if exclude_id is not None:
        stmt = stmt.where(ModelVersion.id != exclude_id)
    db.execute(stmt)


def register_model_from_run(
    db: Session,
    run_id: str,
    *,
    artifacts_root: Path,
    kind: str = ModelVersionKind.PREGAME.value,
    activate: bool = False,
) -> ModelVersion:
    """
    Insert or update a ``ModelVersion`` row from ``artifacts_root/<run_id>/``.

    When ``activate`` is True, archives other active rows of the same ``kind``.
    """
    run_dir = artifacts_root / run_id
    metrics, manifest, model_path = _load_run_artifacts(run_dir)

    manifest_run_id = manifest.get("run_id")
    if manifest_run_id != run_id:
        raise ArtifactError(
            f"manifest run_id {manifest_run_id!r} does not match requested {run_id!r}"
        )

    fields = {
        "artifact_path": str(model_path),
        "kind": kind,
        "metrics": metrics,
        "feature_columns": manifest.get("feature_columns"),
        "hyperparameters": manifest.get("hyperparameters"),
        "train_seasons": manifest.get("seasons"),
        "git_hash": manifest.get("git_hash"),
    }

    existing = db.scalar(select(ModelVersion).where(ModelVersion.run_id == run_id))
    if existing is None:
        model_version = ModelVersion(
            run_id=run_id,
            status=ModelVersionStatus.ARCHIVED.value,
            **fields,
        )
        db.add(model_version)
        db.flush()
    else:
        for key, value in fields.items():
            setattr(existing, key, value)
        model_version = existing

    if activate:
        _archive_active_for_kind(db, kind, exclude_id=model_version.id)
        model_version.status = ModelVersionStatus.ACTIVE.value

    db.commit()
    db.refresh(model_version)
    return model_version


def activate_model_version(db: Session, model_version: ModelVersion) -> ModelVersion:
    """Set the given model version active and archive other active rows of the same kind."""
    _archive_active_for_kind(db, model_version.kind, exclude_id=model_version.id)
    model_version.status = ModelVersionStatus.ACTIVE.value
    db.commit()
    db.refresh(model_version)
    return model_version


def get_active_model_version(
    db: Session,
    *,
    kind: str = ModelVersionKind.PREGAME.value,
) -> ModelVersion:
    """Return the sole active model version for ``kind``."""
    rows = db.scalars(
        select(ModelVersion).where(
            ModelVersion.kind == kind,
            ModelVersion.status == ModelVersionStatus.ACTIVE.value,
        )
    ).all()
    if len(rows) == 0:
        raise ModelVersionNotFoundError(f"No active {kind} model version registered")
    if len(rows) > 1:
        run_ids = [row.run_id for row in rows]
        raise ModelVersionNotFoundError(
            f"Multiple active {kind} model versions: {run_ids}"
        )
    return rows[0]


def get_active_pregame_model(db: Session) -> ModelVersion:
    """Return the active pregame model version for inference."""
    return get_active_model_version(db, kind=ModelVersionKind.PREGAME.value)
