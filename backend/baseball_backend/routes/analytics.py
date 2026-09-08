"""Cross-entity analytics routes (matchups)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from baseball_backend.db.models import User
from baseball_backend.db.session import get_db
from baseball_backend.deps import get_current_user
from baseball_backend.schemas import MatchupAnalyticsRead
from baseball_backend.services.analytics_service import (
    TeamNotFoundError,
    get_matchup_analytics,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/matchup", response_model=MatchupAnalyticsRead)
def get_matchup(
    home_team_id: int = Query(..., ge=1),
    away_team_id: int = Query(..., ge=1),
    season: int = Query(..., ge=2000, le=2100),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> MatchupAnalyticsRead:
    try:
        payload = get_matchup_analytics(
            db,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            season=season,
        )
    except TeamNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return MatchupAnalyticsRead.model_validate(payload)
