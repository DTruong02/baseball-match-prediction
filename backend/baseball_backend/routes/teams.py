"""Team catalog and analytics routes."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from baseball_backend.db.models import Team, User
from baseball_backend.db.session import get_db
from baseball_backend.deps import get_current_user
from baseball_backend.schemas import TeamAnalyticsRead, TeamRead
from baseball_backend.services.analytics_service import (
    TeamNotFoundError,
    get_team_analytics,
)

router = APIRouter(prefix="/teams", tags=["teams"])


@router.get("", response_model=list[TeamRead])
def list_teams(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> list[Team]:
    return list(db.scalars(select(Team).order_by(Team.abbreviation.asc(), Team.id.asc())).all())


@router.get("/{team_id}", response_model=TeamRead)
def get_team(
    team_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> Team:
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Team not found: id={team_id}",
        )
    return team


@router.get("/{team_id}/analytics", response_model=TeamAnalyticsRead)
def get_team_analytics_route(
    team_id: int,
    season: int = Query(..., ge=2000, le=2100),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> TeamAnalyticsRead:
    try:
        payload = get_team_analytics(db, team_id, season)
    except TeamNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return TeamAnalyticsRead.model_validate(payload)
