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
from baseball_backend.services.schedule_sync import (
    _team_name_and_city,
    sync_schedule_for_date,
    sync_schedule_for_date_range,
    sync_schedule_for_season,
)

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
        "name": "New York Yankees",
        "teamName": "Yankees",
        "clubName": "Yankees",
        "locationName": "Bronx",
    },
    {
        "id": 111,
        "abbreviation": "BOS",
        "name": "Boston Red Sox",
        "teamName": "Red Sox",
        "clubName": "Red Sox",
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


def test_team_name_and_city_prefers_nickname() -> None:
    name, city = _team_name_and_city(
        {
            "name": "Toronto Blue Jays",
            "teamName": "Blue Jays",
            "clubName": "Blue Jays",
            "locationName": "Toronto",
        },
        "TOR",
    )
    assert name == "Blue Jays"
    assert city == "Toronto"


def test_team_name_and_city_strips_city_from_full_name() -> None:
    name, city = _team_name_and_city(
        {"name": "Toronto Blue Jays", "locationName": "Toronto"},
        "TOR",
    )
    assert name == "Blue Jays"
    assert city == "Toronto"


def test_team_name_and_city_uses_branded_state_not_venue_city() -> None:
    name, city = _team_name_and_city(
        {
            "name": "Arizona Diamondbacks",
            "teamName": "D-backs",
            "clubName": "Diamondbacks",
            "locationName": "Phoenix",
        },
        "AZ",
    )
    assert name == "Diamondbacks"
    assert city == "Arizona"

    name, city = _team_name_and_city(
        {
            "name": "Texas Rangers",
            "teamName": "Rangers",
            "clubName": "Rangers",
            "locationName": "Arlington",
        },
        "TEX",
    )
    assert name == "Rangers"
    assert city == "Texas"

    name, city = _team_name_and_city(
        {
            "name": "Tampa Bay Rays",
            "teamName": "Rays",
            "clubName": "Rays",
            "locationName": "St. Petersburg",
        },
        "TB",
    )
    assert name == "Rays"
    assert city == "Tampa Bay"


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
    assert teams[0].name == "Red Sox"
    assert teams[0].city == "Boston"

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


SAMPLE_GAME_JUL = ScheduledGame(
    game_pk=778002,
    game_date="2025-07-04",
    season=2025,
    status="Final",
    detailed_state="Final",
    home_team_id=147,
    away_team_id=111,
    home_abbrev="NYY",
    away_abbrev="BOS",
    venue_id=3313,
    venue_name="Yankee Stadium",
    home_probable_id=None,
    away_probable_id=None,
    home_score=5,
    away_score=3,
)

SAMPLE_GAME_OTHER_SEASON = ScheduledGame(
    game_pk=778003,
    game_date="2024-09-01",
    season=2024,
    status="Final",
    detailed_state="Final",
    home_team_id=147,
    away_team_id=111,
    home_abbrev="NYY",
    away_abbrev="BOS",
    venue_id=3313,
    venue_name="Yankee Stadium",
    home_probable_id=None,
    away_probable_id=None,
    home_score=2,
    away_score=1,
)


@patch("baseball_backend.services.schedule_sync.fetch_teams", return_value=TEAMS_PAYLOAD)
@patch(
    "baseball_backend.services.schedule_sync.fetch_schedule_season",
    return_value=[SAMPLE_GAME, SAMPLE_GAME_JUL],
)
def test_sync_schedule_for_season(
    _mock_season: object,
    _mock_teams: object,
    db_session: Session,
) -> None:
    count = sync_schedule_for_season(db_session, 2025)
    assert count == 2
    assert len(db_session.scalars(select(Game)).all()) == 2
    jul = db_session.scalar(select(Game).where(Game.game_pk == 778002))
    assert jul is not None
    assert jul.winner == "NYY"
    assert jul.home_score == 5


@patch("baseball_backend.services.schedule_sync.fetch_teams", return_value=TEAMS_PAYLOAD)
def test_sync_schedule_for_date_range_filters_and_fetches_seasons(
    _mock_teams: object,
    db_session: Session,
) -> None:
    def _season_side_effect(season: int, **_kwargs: object):
        if season == 2024:
            return iter([SAMPLE_GAME_OTHER_SEASON])
        if season == 2025:
            return iter([SAMPLE_GAME, SAMPLE_GAME_JUL])
        return iter([])

    with patch(
        "baseball_backend.services.schedule_sync.fetch_schedule_season",
        side_effect=_season_side_effect,
    ) as mock_season:
        count = sync_schedule_for_date_range(db_session, "2025-04-01", "2025-07-31")

    assert count == 2
    assert mock_season.call_count == 1  # only 2025 overlaps Apr–Jul 2025
    pks = {g.game_pk for g in db_session.scalars(select(Game)).all()}
    assert pks == {778001, 778002}


@patch("baseball_backend.services.schedule_sync.fetch_teams", return_value=TEAMS_PAYLOAD)
def test_sync_schedule_for_date_range_rejects_inverted_bounds(
    _mock_teams: object,
    db_session: Session,
) -> None:
    with pytest.raises(ValueError, match="before start"):
        sync_schedule_for_date_range(db_session, "2025-08-01", "2025-04-01")


@patch("baseball_backend.services.schedule_sync.fetch_teams", return_value=TEAMS_PAYLOAD)
@patch(
    "baseball_backend.services.schedule_sync.fetch_schedule_season",
    return_value=[SAMPLE_GAME, SAMPLE_GAME, SAMPLE_GAME_JUL],
)
def test_sync_schedule_dedupes_duplicate_game_pks(
    _mock_season: object,
    _mock_teams: object,
    db_session: Session,
) -> None:
    count = sync_schedule_for_season(db_session, 2025)
    assert count == 2
    assert len(db_session.scalars(select(Game)).all()) == 2


@patch("baseball_backend.services.schedule_sync.fetch_teams", return_value=TEAMS_PAYLOAD)
@patch(
    "baseball_backend.services.schedule_sync.fetch_schedule_season",
    return_value=[SAMPLE_GAME, SAMPLE_GAME_JUL],
)
def test_sync_schedule_updates_existing_game_pk(
    _mock_season: object,
    _mock_teams: object,
    db_session: Session,
) -> None:
    with patch(
        "baseball_backend.services.schedule_sync.fetch_schedule_for_date",
        return_value=[SAMPLE_GAME],
    ):
        sync_schedule_for_date(db_session, "2025-04-06")

    count = sync_schedule_for_season(db_session, 2025)
    assert count == 2
    assert len(db_session.scalars(select(Game)).all()) == 2
    game = db_session.scalar(select(Game).where(Game.game_pk == 778001))
    assert game is not None
    assert game.status == "Preview"


TODAY_SLATE_GAME = ScheduledGame(
    game_pk=778010,
    game_date="2025-04-07",
    season=2025,
    status="Preview",
    detailed_state="Scheduled",
    home_team_id=147,
    away_team_id=111,
    home_abbrev="NYY",
    away_abbrev="BOS",
    venue_id=3313,
    venue_name="Yankee Stadium",
    home_probable_id=None,
    away_probable_id=None,
)

MISDATED_FINAL = ScheduledGame(
    game_pk=778099,
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
    home_probable_id=None,
    away_probable_id=None,
    home_score=4,
    away_score=2,
)


@patch("baseball_backend.services.schedule_sync.fetch_teams", return_value=TEAMS_PAYLOAD)
@patch(
    "baseball_backend.services.schedule_sync.fetch_schedule_by_game_pk",
    return_value=MISDATED_FINAL,
)
@patch(
    "baseball_backend.services.schedule_sync.fetch_schedule_for_date",
    return_value=[TODAY_SLATE_GAME],
)
def test_sync_schedule_reconciles_misdated_games_on_slate(
    _mock_schedule: object,
    mock_by_pk: object,
    _mock_teams: object,
    db_session: Session,
) -> None:
    """Games stored on today's date but belonging to yesterday move on sync."""
    db_session.add(
        Team(id=147, abbreviation="NYY", name="Yankees", city="New York")
    )
    db_session.add(
        Team(id=111, abbreviation="BOS", name="Red Sox", city="Massachusetts")
    )
    db_session.add(
        Game(
            game_pk=778099,
            game_date=date(2025, 4, 7),  # wrongly stored as next UTC day
            season=2025,
            status="Final",
            detailed_state="Final",
            home_team_id=147,
            away_team_id=111,
            home_score=4,
            away_score=2,
            winner="NYY",
        )
    )
    db_session.commit()

    count = sync_schedule_for_date(db_session, "2025-04-07")
    assert count == 2  # today's slate game + reconciled misdated final
    mock_by_pk.assert_called_once_with(778099)

    misdated = db_session.scalar(select(Game).where(Game.game_pk == 778099))
    assert misdated is not None
    assert misdated.game_date == date(2025, 4, 6)

    today_game = db_session.scalar(select(Game).where(Game.game_pk == 778010))
    assert today_game is not None
    assert today_game.game_date == date(2025, 4, 7)

    on_today = {
        g.game_pk
        for g in db_session.scalars(
            select(Game).where(Game.game_date == date(2025, 4, 7))
        ).all()
    }
    assert on_today == {778010}
