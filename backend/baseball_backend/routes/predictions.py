"""Prediction routes."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from baseball_backend.db.models import User
from baseball_backend.db.session import get_db
from baseball_backend.deps import get_current_user
from baseball_backend.schemas import PredictionRead
from baseball_backend.services.prediction_service import (
    GameNotFoundError,
    get_prediction_for_game_pk,
)

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.get("/{game_pk}", response_model=Optional[PredictionRead])
def get_prediction(
    game_pk: int,
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> Optional[PredictionRead]:
    """Return the active pregame prediction for a game, generating one if needed."""
    try:
        prediction = get_prediction_for_game_pk(db, game_pk)
    except GameNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Game not found") from None

    if prediction is None:
        return None
    return PredictionRead.from_prediction(prediction)
