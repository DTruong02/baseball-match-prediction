"""Metadata and index coverage for the initial database schema."""

from baseball_backend.db import Base, Game, User


def test_all_tables_registered() -> None:
    expected = {
        "users",
        "teams",
        "players",
        "games",
        "predictions",
        "model_versions",
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

    game_pk_unique = any(
        constraint.name == "uq_games_game_pk"
        for constraint in Game.__table__.constraints
        if hasattr(constraint, "name")
    )
    assert game_pk_unique
