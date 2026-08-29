"""Prediction routes (stub until Stage 3)."""

from fastapi import APIRouter, Depends

from baseball_backend.db.models import User
from baseball_backend.deps import get_current_user

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.get("/{game_pk}")
def get_prediction(
    game_pk: int,
    _current_user: User = Depends(get_current_user),
) -> None:
    """Return null until pregame predictions are wired in Stage 3."""
    return None
