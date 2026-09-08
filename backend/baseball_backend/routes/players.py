"""Player detail and analytics routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from baseball_backend.db.models import Player, User
from baseball_backend.db.session import get_db
from baseball_backend.deps import get_current_user
from baseball_backend.schemas import PlayerAnalyticsRead, PlayerDetailRead
from baseball_backend.services.analytics_service import (
    PlayerNotFoundError,
    get_player_analytics,
)

router = APIRouter(prefix="/players", tags=["players"])


@router.get("/{player_id}", response_model=PlayerDetailRead)
def get_player(
    player_id: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> PlayerDetailRead:
    player = db.scalars(
        select(Player).options(joinedload(Player.team)).where(Player.id == player_id)
    ).first()
    if player is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Player not found: id={player_id}",
        )
    return PlayerDetailRead(
        id=player.id,
        full_name=player.full_name,
        primary_position=player.primary_position,
        team=player.team,
    )


@router.get("/{player_id}/analytics", response_model=PlayerAnalyticsRead)
def get_player_analytics_route(
    player_id: int,
    season: int = Query(..., ge=2000, le=2100),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> PlayerAnalyticsRead:
    try:
        payload = get_player_analytics(db, player_id, season)
    except PlayerNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return PlayerAnalyticsRead.model_validate(payload)
