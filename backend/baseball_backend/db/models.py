"""ORM models for users, MLB entities, predictions, and model registry."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from baseball_backend.db.base import Base


class ModelVersionStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ModelVersionKind(str, Enum):
    PREGAME = "pregame"
    IN_GAME = "in_game"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Team(Base):
    """MLB team; ``id`` is the MLB Stats API team id."""

    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    abbreviation: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    city: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    home_games: Mapped[list["Game"]] = relationship(
        "Game",
        back_populates="home_team",
        foreign_keys="Game.home_team_id",
    )
    away_games: Mapped[list["Game"]] = relationship(
        "Game",
        back_populates="away_team",
        foreign_keys="Game.away_team_id",
    )
    players: Mapped[list["Player"]] = relationship(back_populates="team")


class Player(Base):
    """MLB player; ``id`` is the MLB Stats API player id."""

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    team_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )
    primary_position: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    team: Mapped[Optional[Team]] = relationship(back_populates="players")


class Game(Base):
    """Scheduled or completed MLB game."""

    __tablename__ = "games"
    __table_args__ = (
        Index("ix_games_game_date", "game_date"),
        UniqueConstraint("game_pk", name="uq_games_game_pk"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_pk: Mapped[int] = mapped_column(Integer, nullable=False)
    game_date: Mapped[date] = mapped_column(Date, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    detailed_state: Mapped[str] = mapped_column(String(64), nullable=False)
    home_team_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False
    )
    away_team_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("teams.id", ondelete="RESTRICT"), nullable=False
    )
    venue_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    venue_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    home_probable_pitcher_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="SET NULL"), nullable=True
    )
    away_probable_pitcher_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="SET NULL"), nullable=True
    )
    home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    winner: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    home_team: Mapped[Team] = relationship(
        "Team", back_populates="home_games", foreign_keys=[home_team_id]
    )
    away_team: Mapped[Team] = relationship(
        "Team", back_populates="away_games", foreign_keys=[away_team_id]
    )
    home_probable_pitcher: Mapped[Optional[Player]] = relationship(
        "Player", foreign_keys=[home_probable_pitcher_id]
    )
    away_probable_pitcher: Mapped[Optional[Player]] = relationship(
        "Player", foreign_keys=[away_probable_pitcher_id]
    )
    predictions: Mapped[list["Prediction"]] = relationship(back_populates="game")


class ModelVersion(Base):
    """Registered ML artifact from Stage 1 training runs."""

    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    artifact_path: Mapped[str] = mapped_column(String(512), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ModelVersionKind.PREGAME.value
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ModelVersionStatus.ARCHIVED.value
    )
    metrics: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    production_metrics: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    feature_columns: Mapped[Optional[list[str]]] = mapped_column(JSONB, nullable=True)
    hyperparameters: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    train_seasons: Mapped[Optional[list[int]]] = mapped_column(JSONB, nullable=True)
    git_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    predictions: Mapped[list["Prediction"]] = relationship(back_populates="model_version")


class Prediction(Base):
    """Pregame or live model output for a game."""

    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint(
            "game_id",
            "model_version_id",
            name="uq_predictions_game_model_version",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    model_version_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("model_versions.id", ondelete="RESTRICT"), nullable=False
    )
    home_win_proba: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    away_win_proba: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    features: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    game: Mapped[Game] = relationship(back_populates="predictions")
    model_version: Mapped[ModelVersion] = relationship(back_populates="predictions")
