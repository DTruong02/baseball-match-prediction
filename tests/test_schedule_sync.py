"""Tests for schedule sync service."""

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

SAMPLE_GAME = ScheduledGame(
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
    home_probable_name="Trevor Williams",
    away_probable_name="Corbin Burnes",
)

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
    return_value=[SAMPLE_GAME],
)
def test_sync_schedule_inserts_teams_games_and_players(
    _mock_schedule: object,
    _mock_teams: object,
    db_session: Session,
) -> None:
    count = sync_schedule_for_date(db_session, "2025-04-06")
    assert count == 1

    teams = db_session.scalars(select(Team).order_by(Team.id)).all()
    assert len(teams) == 2
    assert teams[0].abbreviation == "BOS"
    assert teams[1].name == "Yankees"
    assert teams[1].city == "New York"

    game = db_session.scalar(select(Game).where(Game.game_pk == 778001))
    assert game is not None
    assert game.game_date == date(2025, 4, 6)
    assert game.venue_name == "Yankee Stadium"
    assert game.home_probable_pitcher_id == 592866
    assert game.away_probable_pitcher_id == 669203

    pitchers = db_session.scalars(select(Player).order_by(Player.id)).all()
    assert len(pitchers) == 2
    assert {pitcher.full_name for pitcher in pitchers} == {"Corbin Burnes", "Trevor Williams"}


@patch("baseball_backend.services.schedule_sync.fetch_teams", return_value=TEAMS_PAYLOAD)
@patch(
    "baseball_backend.services.schedule_sync.fetch_schedule_for_date",
    return_value=[SAMPLE_GAME],
)
def test_sync_schedule_is_idempotent(
    _mock_schedule: object,
    _mock_teams: object,
    db_session: Session,
) -> None:
    sync_schedule_for_date(db_session, "2025-04-06")
    sync_schedule_for_date(db_session, "2025-04-06")

    assert db_session.scalar(select(Game).where(Game.game_pk == 778001)) is not None
    assert len(db_session.scalars(select(Game)).all()) == 1
    assert len(db_session.scalars(select(Team)).all()) == 2
