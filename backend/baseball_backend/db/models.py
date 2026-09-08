"""ORM models for users, MLB entities, predictions, and model registry."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    JSON,
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


class FollowEntityType(str, Enum):
    TEAM = "team"
    PLAYER = "player"


class NotificationChannel(str, Enum):
    IN_APP = "in_app"
    EMAIL = "email"


class NotificationDeliveryStatus(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


class NotificationAlertType(str, Enum):
    GAME_START = "game_start"
    WP_THRESHOLD = "wp_threshold"
    HIGH_LEVERAGE = "high_leverage"
    GAME_FINAL = "game_final"
    NEW_PREDICTION = "new_prediction"


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

    follows: Mapped[list["UserFollow"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    notification_preference: Mapped[Optional["NotificationPreference"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
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
    follows: Mapped[list["UserFollow"]] = relationship(back_populates="team")


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
    follows: Mapped[list["UserFollow"]] = relationship(back_populates="player")


class UserFollow(Base):
    """User watchlist entry for a team or player."""

    __tablename__ = "user_follows"
    __table_args__ = (
        CheckConstraint(
            "(entity_type = 'team' AND team_id IS NOT NULL AND player_id IS NULL) "
            "OR (entity_type = 'player' AND player_id IS NOT NULL AND team_id IS NULL)",
            name="ck_user_follows_entity_target",
        ),
        Index(
            "uq_user_follows_user_team",
            "user_id",
            "team_id",
            unique=True,
            postgresql_where="team_id IS NOT NULL",
            sqlite_where="team_id IS NOT NULL",
        ),
        Index(
            "uq_user_follows_user_player",
            "user_id",
            "player_id",
            unique=True,
            postgresql_where="player_id IS NOT NULL",
            sqlite_where="player_id IS NOT NULL",
        ),
        Index("ix_user_follows_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    team_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=True
    )
    player_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("players.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="follows")
    team: Mapped[Optional[Team]] = relationship(back_populates="follows")
    player: Mapped[Optional[Player]] = relationship(back_populates="follows")


class NotificationPreference(Base):
    """Per-user channel and alert-type toggles for notification delivery."""

    __tablename__ = "notification_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notify_game_start: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_wp_threshold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_high_leverage: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    notify_game_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_new_prediction: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    wp_threshold_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.15)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="notification_preference")


class Notification(Base):
    """In-app or email notification queued for (or already shown to) a user."""

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_id_created_at", "user_id", "created_at"),
        Index("ix_notifications_channel_status", "channel", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    alert_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=NotificationDeliveryStatus.PENDING.value,
    )
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="notifications")


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
    live_state: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
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
    events: Mapped[list["GameEvent"]] = relationship(
        back_populates="game",
        foreign_keys="GameEvent.game_pk",
        primaryjoin="Game.game_pk == GameEvent.game_pk",
        viewonly=True,
    )


class GameEvent(Base):
    """Normalized play-by-play or state event from MLB live feed."""

    __tablename__ = "game_events"
    __table_args__ = (
        UniqueConstraint("game_pk", "event_id", name="uq_game_events_game_pk_event_id"),
        Index("ix_game_events_game_pk_sequence", "game_pk", "sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_pk: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    game: Mapped[Optional["Game"]] = relationship(
        "Game",
        back_populates="events",
        foreign_keys=[game_pk],
        primaryjoin="GameEvent.game_pk == Game.game_pk",
        viewonly=True,
    )


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
