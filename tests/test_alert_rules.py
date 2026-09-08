"""Tests for Stage 6.3 alert rules."""

from collections.abc import Generator
from datetime import date

import pytest
from sqlalchemy import JSON, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from baseball_backend.db.base import Base
from baseball_backend.db.models import (
    AlertDispatch,
    FollowEntityType,
    Game,
    Notification,
    NotificationAlertType,
    NotificationChannel,
    NotificationPreference,
    Player,
    Prediction,
    Team,
    User,
    UserFollow,
    ModelVersion,
    ModelVersionKind,
    ModelVersionStatus,
)
from baseball_backend.services.alert_rules import (
    evaluate_live_game_alerts,
    evaluate_new_prediction_alert,
    evaluate_schedule_status_alerts,
    follower_user_ids_for_game,
)
from baseball_backend.services.live_normalize import GameLiveState
from baseball_backend.services.notification_service import get_or_create_preferences


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    jsonb_columns: list = []
    for table in (
        Notification.__table__,
        Prediction.__table__,
        ModelVersion.__table__,
    ):
        for column in table.columns:
            if isinstance(column.type, JSONB):
                jsonb_columns.append(column)
                column.type = JSON()
    tables = [
        User.__table__,
        Team.__table__,
        Player.__table__,
        UserFollow.__table__,
        NotificationPreference.__table__,
        Notification.__table__,
        AlertDispatch.__table__,
        Game.__table__,
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
        Base.metadata.drop_all(bind=engine, tables=tables)
        engine.dispose()
        for column in jsonb_columns:
            column.type = JSONB()


def _seed_user(db: Session, email: str = "fan@example.com") -> User:
    user = User(email=email, hashed_password="x")
    db.add(user)
    db.commit()
    db.refresh(user)
    get_or_create_preferences(db, user.id)
    return user


def _seed_game(db: Session, *, status: str = "Preview", detailed: str = "Scheduled") -> Game:
    game = Game(
        game_pk=824239,
        game_date=date(2026, 8, 15),
        season=2026,
        status=status,
        detailed_state=detailed,
        home_team_id=147,
        away_team_id=111,
    )
    db.add(game)
    db.commit()
    db.refresh(game)
    return game


def _follow_team(db: Session, user: User, team_id: int) -> None:
    db.add(
        UserFollow(
            user_id=user.id,
            entity_type=FollowEntityType.TEAM.value,
            team_id=team_id,
        )
    )
    db.commit()


def _state(
    *,
    status: str = "Live",
    detailed: str = "In Progress",
    inning: int = 8,
    home: int = 3,
    away: int = 2,
    on_1b: bool = True,
    on_2b: bool = False,
    on_3b: bool = False,
    is_top: bool = False,
) -> GameLiveState:
    return GameLiveState(
        home_score=home,
        away_score=away,
        status=status,
        detailed_state=detailed,
        current_inning=inning,
        inning_state="Bottom",
        is_top_inning=is_top,
        outs=1,
        balls=0,
        strikes=0,
        on_1b=on_1b,
        on_2b=on_2b,
        on_3b=on_3b,
    )


def test_follower_user_ids_for_game(db_session: Session) -> None:
    user = _seed_user(db_session)
    other = _seed_user(db_session, email="other@example.com")
    game = _seed_game(db_session)
    _follow_team(db_session, user, 147)
    _follow_team(db_session, other, 136)  # unrelated team

    assert follower_user_ids_for_game(db_session, game) == {user.id}


def test_game_start_and_final_alerts_idempotent(db_session: Session) -> None:
    user = _seed_user(db_session)
    game = _seed_game(db_session)
    _follow_team(db_session, user, 147)

    prev = _state(status="Preview", detailed="Scheduled", inning=1, on_1b=False)
    live = _state(status="Live", detailed="Warmup", inning=1, on_1b=False)
    counts = evaluate_live_game_alerts(
        db_session,
        game,
        previous_state=prev,
        new_state=live,
        previous_home_wp=None,
        new_home_wp=None,
        live_wp_updated=False,
    )
    assert counts["game_start"] == 1

    again = evaluate_live_game_alerts(
        db_session,
        game,
        previous_state=prev,
        new_state=live,
        previous_home_wp=None,
        new_home_wp=None,
        live_wp_updated=False,
    )
    assert again["game_start"] == 0

    game.status = "Final"
    game.detailed_state = "Final"
    game.home_score = 5
    game.away_score = 3
    game.winner = "NYY"
    db_session.commit()

    final_state = _state(status="Final", detailed="Final", inning=9, home=5, away=3, on_1b=False)
    counts = evaluate_live_game_alerts(
        db_session,
        game,
        previous_state=live,
        new_state=final_state,
        previous_home_wp=None,
        new_home_wp=None,
        live_wp_updated=False,
    )
    assert counts["game_final"] == 1

    types = {
        row.alert_type
        for row in db_session.scalars(
            select(Notification).where(Notification.user_id == user.id)
        ).all()
    }
    assert types == {
        NotificationAlertType.GAME_START.value,
        NotificationAlertType.GAME_FINAL.value,
    }


def test_wp_threshold_respects_user_pref(db_session: Session) -> None:
    user = _seed_user(db_session)
    game = _seed_game(db_session, status="Live", detailed="In Progress")
    _follow_team(db_session, user, 147)
    prefs = get_or_create_preferences(db_session, user.id)
    prefs.wp_threshold_pct = 0.15
    db_session.commit()

    live = _state()
    # 10pp swing — below default 15pp
    counts = evaluate_live_game_alerts(
        db_session,
        game,
        previous_state=live,
        new_state=live,
        previous_home_wp=0.50,
        new_home_wp=0.60,
        live_wp_updated=True,
    )
    assert counts["wp_threshold"] == 0

    counts = evaluate_live_game_alerts(
        db_session,
        game,
        previous_state=live,
        new_state=live,
        previous_home_wp=0.50,
        new_home_wp=0.70,
        live_wp_updated=True,
    )
    assert counts["wp_threshold"] == 1


def test_high_leverage_requires_late_runners_close(db_session: Session) -> None:
    user = _seed_user(db_session)
    game = _seed_game(db_session, status="Live", detailed="In Progress")
    _follow_team(db_session, user, 111)

    early = _state(inning=5, on_1b=True, home=3, away=2)
    counts = evaluate_live_game_alerts(
        db_session,
        game,
        previous_state=None,
        new_state=early,
        previous_home_wp=None,
        new_home_wp=None,
        live_wp_updated=False,
    )
    assert counts["high_leverage"] == 0

    blowout = _state(inning=8, on_1b=True, home=8, away=1)
    counts = evaluate_live_game_alerts(
        db_session,
        game,
        previous_state=None,
        new_state=blowout,
        previous_home_wp=None,
        new_home_wp=None,
        live_wp_updated=False,
    )
    assert counts["high_leverage"] == 0

    leverage = _state(inning=8, on_1b=True, on_2b=True, home=3, away=2)
    counts = evaluate_live_game_alerts(
        db_session,
        game,
        previous_state=None,
        new_state=leverage,
        previous_home_wp=None,
        new_home_wp=None,
        live_wp_updated=False,
    )
    assert counts["high_leverage"] == 1

    # Same situation again — deduped
    counts = evaluate_live_game_alerts(
        db_session,
        game,
        previous_state=None,
        new_state=leverage,
        previous_home_wp=None,
        new_home_wp=None,
        live_wp_updated=False,
    )
    assert counts["high_leverage"] == 0


def test_schedule_status_alerts(db_session: Session) -> None:
    user = _seed_user(db_session)
    game = _seed_game(db_session, status="Preview", detailed="Scheduled")
    _follow_team(db_session, user, 147)

    game.status = "Live"
    game.detailed_state = "In Progress"
    db_session.commit()

    counts = evaluate_schedule_status_alerts(
        db_session,
        game,
        previous_status="Preview",
        previous_detailed_state="Scheduled",
    )
    assert counts["game_start"] == 1


def test_new_prediction_alert(db_session: Session) -> None:
    user = _seed_user(db_session)
    game = _seed_game(db_session)
    _follow_team(db_session, user, 147)

    model = ModelVersion(
        run_id="run-test",
        artifact_path="/tmp/model.joblib",
        kind=ModelVersionKind.PREGAME.value,
        status=ModelVersionStatus.ACTIVE.value,
    )
    db_session.add(model)
    db_session.commit()

    prediction = Prediction(
        game_id=game.id,
        model_version_id=model.id,
        home_win_proba=0.62,
        away_win_proba=0.38,
    )
    db_session.add(prediction)
    db_session.commit()
    db_session.refresh(prediction)

    created = evaluate_new_prediction_alert(db_session, game, prediction)
    assert created == 1
    assert (
        evaluate_new_prediction_alert(db_session, game, prediction) == 0
    )

    row = db_session.scalar(
        select(Notification).where(
            Notification.alert_type == NotificationAlertType.NEW_PREDICTION.value
        )
    )
    assert row is not None
    assert row.channel == NotificationChannel.IN_APP.value
    assert db_session.scalar(select(AlertDispatch)) is not None


def test_disabled_alert_type_still_claims_dispatch(db_session: Session) -> None:
    user = _seed_user(db_session)
    game = _seed_game(db_session)
    _follow_team(db_session, user, 147)
    prefs = get_or_create_preferences(db_session, user.id)
    prefs.notify_game_start = False
    db_session.commit()

    prev = _state(status="Preview", detailed="Scheduled", inning=1, on_1b=False)
    live = _state(status="Live", detailed="Warmup", inning=1, on_1b=False)
    counts = evaluate_live_game_alerts(
        db_session,
        game,
        previous_state=prev,
        new_state=live,
        previous_home_wp=None,
        new_home_wp=None,
        live_wp_updated=False,
    )
    # claim succeeds but no notification rows
    assert counts["game_start"] == 0
    assert db_session.scalar(select(AlertDispatch)) is not None
    assert db_session.scalar(select(Notification)) is None
