"""Model registry and performance routes."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from baseball_backend.db.models import User
from baseball_backend.db.session import get_db
from baseball_backend.deps import get_current_user
from baseball_backend.schemas import ModelPerformanceRead
from baseball_backend.services.model_registry import ModelVersionNotFoundError
from baseball_backend.services.performance_service import compute_model_performance

router = APIRouter(prefix="/model", tags=["model"])


def _parse_confidence_band(
    confidence_band: str | None,
) -> tuple[float | None, float | None]:
    if confidence_band is None:
        return None, None
    parts = confidence_band.split("-", maxsplit=1)
    if len(parts) != 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="confidence_band must be formatted as min-max (e.g. 0.5-0.6)",
        )
    try:
        low = float(parts[0])
        high = float(parts[1])
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="confidence_band bounds must be numbers",
        ) from exc
    if not 0.0 <= low <= high <= 1.0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="confidence_band bounds must satisfy 0 <= min <= max <= 1",
        )
    return low, high


@router.get("/performance", response_model=ModelPerformanceRead)
def get_model_performance(
    season: Optional[int] = Query(None, ge=2000, le=2100),
    team: Optional[str] = Query(None, min_length=2, max_length=8),
    confidence_band: Optional[str] = Query(
        None,
        description="Predicted-winner confidence range, e.g. 0.5-0.6",
    ),
    confidence_min: Optional[float] = Query(None, ge=0.0, le=1.0),
    confidence_max: Optional[float] = Query(None, ge=0.0, le=1.0),
    model_version_id: Optional[int] = Query(None, ge=1),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> ModelPerformanceRead:
    """Return accuracy, log loss, and calibration for scored predictions."""
    band_low, band_high = _parse_confidence_band(confidence_band)
    resolved_min = confidence_min if confidence_min is not None else band_low
    resolved_max = confidence_max if confidence_max is not None else band_high

    try:
        performance = compute_model_performance(
            db,
            model_version_id=model_version_id,
            season=season,
            team_abbrev=team.upper() if team else None,
            confidence_min=resolved_min,
            confidence_max=resolved_max,
        )
    except ModelVersionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return ModelPerformanceRead.model_validate(performance)
