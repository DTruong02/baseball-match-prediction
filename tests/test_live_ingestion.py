"""Tests for live ingestion service."""

from collections.abc import Generator
from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import JSON, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from baseball_analyze.data.mlb_client import MLBAPIError, ScheduledGame
from baseball_backend.db.base import Base
from baseball_backend.db.models import Game, GameEvent, ModelVersion, Prediction, Team
from baseball_backend.services.live_ingestion import (
    list_live_game_pks_for_date,
    sync_live_game,
    sync_live_games_for_date,
)

LIVE_FEED_FIXTURE = (
    __import__("pathlib").Path(__file__).parent / "fixtures" / "live_feed_sample.json"
)
LIVE_FEED = __import__("json").loads(LIVE_FEED_FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    jsonb_columns: list = []
    for table in (GameEvent.__table__, ModelVersion.__table__, Prediction.__table__):
        for column in table.columns:
            if isinstance(column.type, JSONB):
                jsonb_columns.append(column)
                column.type = JSON()
    tables = [
        Team.__table__,
        Game.__table__,
        GameEvent.__table__,
        ModelVersion.__table__,
        Prediction.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        home = Team(id=147, abbreviation="NYY", name="Yankees", city="New York")
        away = Team(id=111, abbreviation="BOS", name="Red Sox", city="Boston")
        session.add_all([home, away])
        session.commit()
        yield session
    finally:
        session.close()
        for column in jsonb_columns:
            column.type = JSONB()


def _seed_game(db: Session, *, status: str = "Live", detailed_state: str = "In Progress") -> Game:
    game = Game(
        game_pk=824239,
        game_date=date(2026, 8, 15),
        season=2026,
        status=status,
        detailed_state=detailed_state,
        home_team_id=147,
        away_team_id=111,
    )
    db.add(game)
    db.commit()
    return game


@patch("baseball_backend.services.live_ingestion.fetch_live_feed", return_value=LIVE_FEED)
def test_sync_live_game_updates_state_and_inserts_events(
    _mock_feed: object,
    db_session: Session,
) -> None:
    _seed_game(db_session)

    summary = sync_live_game(db_session, 824239)
    assert summary["events_inserted"] == 8
    assert summary["status"] == "Final"

    game = db_session.scalar(select(Game).where(Game.game_pk == 824239))
    assert game is not None
    assert game.home_score == 3
    assert game.away_score == 4
    assert game.winner == "BOS"

    events = db_session.scalars(
        select(GameEvent).where(GameEvent.game_pk == 824239).order_by(GameEvent.sequence)
    ).all()
    assert len(events) == 8
    assert events[0].event_id == "play-0"


@patch("baseball_backend.services.live_ingestion.fetch_live_feed", return_value=LIVE_FEED)
@patch("baseball_backend.services.live_ingestion.cache_live_state")
def test_sync_live_game_persists_live_state_snapshot(
    mock_cache: object,
    _mock_feed: object,
    db_session: Session,
) -> None:
    _seed_game(db_session)

    sync_live_game(db_session, 824239, retries=0)

    game = db_session.scalar(select(Game).where(Game.game_pk == 824239))
    assert game is not None
    assert game.live_state is not None
    assert game.live_state["home_score"] == 3
    assert game.live_state["away_score"] == 4
    assert game.live_state["current_inning"] is not None
    mock_cache.assert_called_once()
    args, kwargs = mock_cache.call_args
    assert args[0] == 824239
    assert args[1].home_score == 3
    assert kwargs["events_inserted"] == 8


@patch(
    "baseball_backend.services.live_ingestion.fetch_live_feed",
    side_effect=MLBAPIError("boom"),
)
@patch("baseball_backend.services.live_ingestion.time.sleep")
def test_sync_live_game_retries_mlb_errors(
    mock_sleep: object,
    mock_feed: object,
    db_session: Session,
) -> None:
    _seed_game(db_session)

    with pytest.raises(MLBAPIError):
        sync_live_game(db_session, 824239, retries=2, backoff_seconds=0.01)

    assert mock_feed.call_count == 3
    assert mock_sleep.call_count == 2


@patch("baseball_backend.services.live_ingestion.fetch_live_feed", return_value=LIVE_FEED)
def test_sync_live_game_is_idempotent_for_events(
    _mock_feed: object,
    db_session: Session,
) -> None:
    _seed_game(db_session)

    sync_live_game(db_session, 824239)
    summary = sync_live_game(db_session, 824239)
    assert summary["events_inserted"] == 0
    assert (
        len(db_session.scalars(select(GameEvent).where(GameEvent.game_pk == 824239)).all())
        == 8
    )


@patch("baseball_backend.services.live_ingestion.fetch_live_feed")
def test_sync_live_game_updates_incomplete_event_payload(
    mock_feed: object,
    db_session: Session,
) -> None:
    """Plays inserted mid-PA should pick up description once MLB fills result."""
    _seed_game(db_session)

    incomplete = {
        "gameData": {
            "status": {"abstractGameState": "Live", "detailedState": "In Progress"}
        },
        "liveData": {
            "linescore": {
                "currentInning": 1,
                "inningState": "Top",
                "isTopInning": True,
                "outs": 0,
                "balls": 0,
                "strikes": 0,
                "teams": {"home": {"runs": 0}, "away": {"runs": 0}},
                "offense": {},
            },
            "plays": {
                "allPlays": [
                    {
                        "about": {
                            "atBatIndex": 0,
                            "inning": 1,
                            "halfInning": "top",
                            "isTopInning": True,
                            "isComplete": False,
                            "isScoringPlay": False,
                        },
                        "result": {},
                        "matchup": {
                            "batter": {"id": 1, "fullName": "Test Batter"},
                            "pitcher": {"id": 2, "fullName": "Test Pitcher"},
                        },
                    }
                ]
            },
        },
    }
    complete = {
        "gameData": {
            "status": {"abstractGameState": "Live", "detailedState": "In Progress"}
        },
        "liveData": {
            "linescore": {
                "currentInning": 1,
                "inningState": "Top",
                "isTopInning": True,
                "outs": 1,
                "balls": 0,
                "strikes": 0,
                "teams": {"home": {"runs": 0}, "away": {"runs": 0}},
                "offense": {},
            },
            "plays": {
                "allPlays": [
                    {
                        "about": {
                            "atBatIndex": 0,
                            "inning": 1,
                            "halfInning": "top",
                            "isTopInning": True,
                            "isComplete": True,
                            "isScoringPlay": False,
                        },
                        "result": {
                            "event": "Strikeout",
                            "eventType": "strikeout",
                            "description": "Test Batter strikes out swinging.",
                            "rbi": 0,
                            "awayScore": 0,
                            "homeScore": 0,
                        },
                        "matchup": {
                            "batter": {"id": 1, "fullName": "Test Batter"},
                            "pitcher": {"id": 2, "fullName": "Test Pitcher"},
                        },
                    }
                ]
            },
        },
    }
    mock_feed.side_effect = [incomplete, complete]

    first = sync_live_game(db_session, 824239, retries=0)
    assert first["events_inserted"] == 1
    row = db_session.scalar(
        select(GameEvent).where(
            GameEvent.game_pk == 824239, GameEvent.event_id == "play-0"
        )
    )
    assert row is not None
    assert not row.payload.get("description")
    assert row.payload.get("batter_name") == "Test Batter"

    second = sync_live_game(db_session, 824239, retries=0)
    assert second["events_inserted"] == 0
    db_session.refresh(row)
    assert row.payload.get("description") == "Test Batter strikes out swinging."
    assert row.payload.get("event") == "Strikeout"
    assert row.payload.get("is_complete") is True


@patch(
    "baseball_backend.services.live_ingestion.fetch_schedule_for_date",
    return_value=[],
)
def test_list_live_game_pks_from_db(
    _mock_schedule: object,
    db_session: Session,
) -> None:
    _seed_game(db_session)
    scheduled_game = Game(
        game_pk=824240,
        game_date=date(2026, 8, 15),
        season=2026,
        status="Preview",
        detailed_state="Scheduled",
        home_team_id=147,
        away_team_id=111,
    )
    db_session.add(scheduled_game)
    db_session.commit()

    pks = list_live_game_pks_for_date(db_session, "2026-08-15")
    assert pks == [824239]


@patch("baseball_backend.services.live_ingestion.fetch_live_feed", return_value=LIVE_FEED)
@patch(
    "baseball_backend.services.live_ingestion.fetch_schedule_for_date",
    return_value=[
        ScheduledGame(
            game_pk=999001,
            game_date="2026-08-15",
            season=2026,
            status="Live",
            detailed_state="In Progress",
            home_team_id=147,
            away_team_id=111,
            home_abbrev="NYY",
            away_abbrev="BOS",
            venue_id=None,
            home_probable_id=None,
            away_probable_id=None,
        )
    ],
)
def test_sync_live_games_for_date_includes_schedule_candidates(
    _mock_schedule: object,
    _mock_feed: object,
    db_session: Session,
) -> None:
    _seed_game(db_session)

    summaries = sync_live_games_for_date(db_session, "2026-08-15", game_delay_seconds=0)
    pks = {summary["game_pk"] for summary in summaries}
    assert 824239 in pks
    assert 999001 in pks
    assert any("error" in summary for summary in summaries if summary["game_pk"] == 999001)


@patch(
    "baseball_backend.services.live_ingestion.fetch_schedule_for_date",
    return_value=[],
)
@patch("baseball_backend.services.live_ingestion.set_live_feed_health")
def test_sync_live_games_for_date_marks_healthy_when_idle(
    mock_set_health: object,
    _mock_schedule: object,
    db_session: Session,
) -> None:
    """Empty poll cycles must clear a stale ok=false feed-health marker."""
    summaries = sync_live_games_for_date(db_session, "2026-08-15", game_delay_seconds=0)
    assert summaries == []
    mock_set_health.assert_called_once_with(ok=True)
