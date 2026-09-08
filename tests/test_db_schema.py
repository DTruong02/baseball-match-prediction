"""Metadata and index coverage for the initial database schema."""

from baseball_backend.db import Base, Game, GameEvent, ModelVersion, Player, Prediction, Team, User


def test_all_tables_registered() -> None:
    expected = {
        "users",
        "teams",
        "players",
        "games",
        "game_events",
        "predictions",
        "model_versions",
        "user_follows",
        "notification_preferences",
        "notifications",
        "alert_dispatches",
    }
    assert expected == set(Base.metadata.tables.keys())


def test_user_email_index_is_unique() -> None:
    indexes = {index.name: index for index in User.__table__.indexes}
    email_index = indexes.get("ix_users_email")
    assert email_index is not None
    assert email_index.unique is True
    assert [column.name for column in email_index.columns] == ["email"]


def test_game_indexes() -> None:
    indexes = {index.name: index for index in Game.__table__.indexes}
    assert "ix_games_game_date" in indexes
    assert [column.name for column in indexes["ix_games_game_date"].columns] == [
        "game_date"
    ]
    assert "ix_games_status_game_date" in indexes
    assert [column.name for column in indexes["ix_games_status_game_date"].columns] == [
        "status",
        "game_date",
    ]

    game_pk_unique = any(
        constraint.name == "uq_games_game_pk"
        for constraint in Game.__table__.constraints
        if hasattr(constraint, "name")
    )
    assert game_pk_unique


def test_game_event_indexes() -> None:
    indexes = {index.name: index for index in GameEvent.__table__.indexes}
    assert "ix_game_events_game_pk_sequence" in indexes
    assert [column.name for column in indexes["ix_game_events_game_pk_sequence"].columns] == [
        "game_pk",
        "sequence",
    ]

    event_unique = any(
        constraint.name == "uq_game_events_game_pk_event_id"
        for constraint in GameEvent.__table__.constraints
        if hasattr(constraint, "name")
    )
    assert event_unique


def test_observability_hot_path_indexes() -> None:
    model_indexes = {index.name: index for index in ModelVersion.__table__.indexes}
    assert "ix_model_versions_kind_status" in model_indexes
    assert [
        column.name for column in model_indexes["ix_model_versions_kind_status"].columns
    ] == ["kind", "status"]

    player_indexes = {index.name: index for index in Player.__table__.indexes}
    assert "ix_players_team_id" in player_indexes

    prediction_indexes = {index.name: index for index in Prediction.__table__.indexes}
    assert "ix_predictions_model_version_id" in prediction_indexes

    team_indexes = {index.name: index for index in Team.__table__.indexes}
    assert "ix_teams_abbreviation" in team_indexes
