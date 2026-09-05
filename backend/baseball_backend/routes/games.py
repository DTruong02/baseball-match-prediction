"""Game schedule routes."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from baseball_backend.db.models import Game, GameEvent, User
from baseball_backend.db.session import get_db
from baseball_backend.deps import get_current_user
from baseball_backend.schemas import (
    GameEventRead,
    GameRead,
    LiveSnapshotRead,
    LiveStateRead,
    ScheduleSyncResponse,
)
from baseball_backend.services.live_ws import resolve_live_snapshot
from baseball_backend.services.prediction_service import generate_missing_predictions_for_date
from baseball_backend.services.schedule_sync import sync_schedule_for_date

router = APIRouter(prefix="/games", tags=["games"])

_GAME_LOAD_OPTIONS = (
    joinedload(Game.home_team),
    joinedload(Game.away_team),
    joinedload(Game.home_probable_pitcher),
    joinedload(Game.away_probable_pitcher),
)


def _game_query():
    return select(Game).options(*_GAME_LOAD_OPTIONS)


def _require_game(db: Session, game_pk: int) -> Game:
    game = db.scalar(_game_query().where(Game.game_pk == game_pk))
    if game is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found")
    return game


@router.get("", response_model=list[GameRead])
def list_games(
    game_date: date = Query(default_factory=date.today, alias="date"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> list[Game]:
    return list(
        db.scalars(
            _game_query()
            .where(Game.game_date == game_date)
            .order_by(Game.game_pk)
        ).all()
    )


@router.post("/sync", response_model=ScheduleSyncResponse)
def sync_games_for_date(
    game_date: date = Query(default_factory=date.today, alias="date"),
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> ScheduleSyncResponse:
    games_synced = sync_schedule_for_date(db, game_date.isoformat())
    predictions_generated = generate_missing_predictions_for_date(db, game_date)
    return ScheduleSyncResponse(
        date=game_date,
        games_synced=games_synced,
        predictions_generated=predictions_generated,
    )


@router.get("/{game_pk}/live", response_model=LiveSnapshotRead)
def get_game_live(
    game_pk: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> LiveSnapshotRead:
    """Scoreboard snapshot for polling when WebSocket is unavailable."""
    _require_game(db, game_pk)
    data, source, degraded = resolve_live_snapshot(db, game_pk)
    return LiveSnapshotRead(
        data=LiveStateRead.model_validate(data) if data is not None else None,
        source=source,
        degraded=degraded,
    )


@router.get("/{game_pk}/events", response_model=list[GameEventRead])
def list_game_events(
    game_pk: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[GameEvent]:
    """Play-by-play events for the live timeline (newest last)."""
    _require_game(db, game_pk)
    return list(
        db.scalars(
            select(GameEvent)
            .where(GameEvent.game_pk == game_pk)
            .order_by(GameEvent.sequence.asc())
            .limit(limit)
        ).all()
    )


@router.get("/{game_pk}", response_model=GameRead)
def get_game(
    game_pk: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> Game:
    return _require_game(db, game_pk)
