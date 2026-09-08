"""User follow / watchlist routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from baseball_backend.db.models import FollowEntityType, Player, Team, User, UserFollow
from baseball_backend.db.session import get_db
from baseball_backend.deps import get_current_user
from baseball_backend.schemas import FollowCreate, FollowRead

router = APIRouter(prefix="/follows", tags=["follows"])

_FOLLOW_LOAD_OPTIONS = (
    joinedload(UserFollow.team),
    joinedload(UserFollow.player),
)


def _follow_query():
    return select(UserFollow).options(*_FOLLOW_LOAD_OPTIONS)


def _to_follow_read(follow: UserFollow) -> FollowRead:
    return FollowRead(
        id=follow.id,
        entity_type=follow.entity_type,
        team=follow.team,
        player=follow.player,
        created_at=follow.created_at,
    )


@router.get("", response_model=list[FollowRead])
def list_follows(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[FollowRead]:
    follows = list(
        db.scalars(
            _follow_query()
            .where(UserFollow.user_id == current_user.id)
            .order_by(UserFollow.created_at.asc(), UserFollow.id.asc())
        ).all()
    )
    return [_to_follow_read(follow) for follow in follows]


@router.post("", response_model=FollowRead, status_code=status.HTTP_201_CREATED)
def create_follow(
    body: FollowCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FollowRead:
    entity_type = body.entity_type.lower()
    if entity_type == FollowEntityType.TEAM.value:
        if body.team_id is None or body.player_id is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="team follows require team_id and must not set player_id",
            )
        team = db.get(Team, body.team_id)
        if team is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
        follow = UserFollow(
            user_id=current_user.id,
            entity_type=FollowEntityType.TEAM.value,
            team_id=body.team_id,
            player_id=None,
        )
    elif entity_type == FollowEntityType.PLAYER.value:
        if body.player_id is None or body.team_id is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="player follows require player_id and must not set team_id",
            )
        player = db.get(Player, body.player_id)
        if player is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Player not found")
        follow = UserFollow(
            user_id=current_user.id,
            entity_type=FollowEntityType.PLAYER.value,
            team_id=None,
            player_id=body.player_id,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="entity_type must be 'team' or 'player'",
        )

    db.add(follow)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Already following this entity",
        ) from exc

    follow = db.scalar(_follow_query().where(UserFollow.id == follow.id))
    assert follow is not None
    return _to_follow_read(follow)


@router.delete("/{follow_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_follow(
    follow_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    follow = db.scalar(
        select(UserFollow).where(
            UserFollow.id == follow_id,
            UserFollow.user_id == current_user.id,
        )
    )
    if follow is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Follow not found")
    db.delete(follow)
    db.commit()
