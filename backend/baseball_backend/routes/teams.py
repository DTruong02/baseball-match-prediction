"""Team catalog routes."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from baseball_backend.db.models import Team, User
from baseball_backend.db.session import get_db
from baseball_backend.deps import get_current_user
from baseball_backend.schemas import TeamRead

router = APIRouter(prefix="/teams", tags=["teams"])


@router.get("", response_model=list[TeamRead])
def list_teams(
    db: Session = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> list[Team]:
    return list(db.scalars(select(Team).order_by(Team.abbreviation.asc(), Team.id.asc())).all())
