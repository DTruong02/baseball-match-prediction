"""Tests for schedule sync final-game outcomes."""

from collections.abc import Generator
from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from baseball_analyze.data.mlb_client import ScheduledGame
from baseball_backend.db.base import Base
from baseball_backend.db.models import Game, Player, Team
from baseball_backend.services.schedule_sync import sync_schedule_for_date

TEAMS_PAYLOAD = [
    {
        "id": 147,
        "abbreviation": "NYY",
        "name": "Yankees",
        "locationName": "New York",
    },
    {
        "id": 111,
        "abbreviation": "BOS",
        "name": "Red Sox",
        "locationName": "Boston",
    },
]

FINAL_GAME = ScheduledGame(
    game_pk=778001,
    game_date="2025-04-06",
    season=2025,
    status="Final",
    detailed_state="Final",
    home_team_id=147,
    away_team_id=111,
    home_abbrev="NYY",
    away_abbrev="BOS",
    venue_id=3313,
    venue_name="Yankee Stadium",
    home_probable_id=592866,
    away_probable_id=669203,
    home_probable_name="Trevor Williams",
    away_probable_name="Corbin Burnes",
    home_score=5,
    away_score=3,
)


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[Team.__table__, Player.__table__, Game.__table__])
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()


@patch("baseball_backend.services.schedule_sync.fetch_teams", return_value=TEAMS_PAYLOAD)
@patch(
    "baseball_backend.services.schedule_sync.fetch_schedule_for_date",
    return_value=[FINAL_GAME],
)
def test_sync_schedule_records_final_outcomes(
    _mock_schedule: object,
    _mock_teams: object,
    db_session: Session,
) -> None:
    sync_schedule_for_date(db_session, "2025-04-06")

    game = db_session.scalar(select(Game).where(Game.game_pk == 778001))
    assert game is not None
    assert game.detailed_state == "Final"
    assert game.home_score == 5
    assert game.away_score == 3
    assert game.winner == "NYY"


@patch("baseball_backend.services.schedule_sync.fetch_teams", return_value=TEAMS_PAYLOAD)
def test_sync_schedule_updates_existing_game_to_final(
    _mock_teams: object,
    db_session: Session,
) -> None:
    scheduled_game = ScheduledGame(
        game_pk=778001,
        game_date="2025-04-06",
        season=2025,
        status="Preview",
        detailed_state="Scheduled",
        home_team_id=147,
        away_team_id=111,
        home_abbrev="NYY",
        away_abbrev="BOS",
        venue_id=3313,
        venue_name="Yankee Stadium",
        home_probable_id=592866,
        away_probable_id=669203,
    )
    with patch(
        "baseball_backend.services.schedule_sync.fetch_schedule_for_date",
        return_value=[scheduled_game],
    ):
        sync_schedule_for_date(db_session, "2025-04-06")

    game = db_session.scalar(select(Game).where(Game.game_pk == 778001))
    assert game is not None
    assert game.winner is None

    with patch(
        "baseball_backend.services.schedule_sync.fetch_schedule_for_date",
        return_value=[FINAL_GAME],
    ):
        sync_schedule_for_date(db_session, "2025-04-06")

    db_session.refresh(game)
    assert game.detailed_state == "Final"
    assert game.home_score == 5
    assert game.away_score == 3
    assert game.winner == "NYY"
