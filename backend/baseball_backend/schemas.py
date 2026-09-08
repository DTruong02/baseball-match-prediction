"""Pydantic request/response schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    sub: str


class TeamRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    abbreviation: str
    name: str
    city: Optional[str] = None


class PlayerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str


class FollowCreate(BaseModel):
    entity_type: str = Field(pattern="^(team|player)$")
    team_id: Optional[int] = None
    player_id: Optional[int] = None


class FollowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_type: str
    team: Optional[TeamRead] = None
    player: Optional[PlayerRead] = None
    created_at: datetime


class GameRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    game_pk: int
    game_date: date
    season: int
    status: str
    detailed_state: str
    home_team: TeamRead
    away_team: TeamRead
    venue_id: Optional[int] = None
    venue_name: Optional[str] = None
    home_probable_pitcher: Optional[PlayerRead] = None
    away_probable_pitcher: Optional[PlayerRead] = None
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    winner: Optional[str] = None
    followed: bool = False


class ModelVersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: str


class PredictionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    game_pk: int
    home_win_proba: float
    away_win_proba: float
    features: Optional[dict[str, float]] = None
    notes: Optional[str] = None
    model_version: ModelVersionSummary
    created_at: datetime

    @classmethod
    def from_prediction(cls, prediction: object) -> "PredictionRead":
        from baseball_backend.db.models import Prediction

        if not isinstance(prediction, Prediction):
            raise TypeError("expected Prediction instance")
        return cls(
            id=prediction.id,
            game_pk=prediction.game.game_pk,
            home_win_proba=prediction.home_win_proba,
            away_win_proba=prediction.away_win_proba,
            features=prediction.features,
            notes=prediction.notes,
            model_version=ModelVersionSummary.model_validate(prediction.model_version),
            created_at=prediction.created_at,
        )


class GameDetailRead(GameRead):
    """Game detail including pregame line and current live win probability."""

    pregame_prediction: Optional[PredictionRead] = None
    live_prediction: Optional[PredictionRead] = None



class ScheduleSyncResponse(BaseModel):
    date: date
    games_synced: int
    predictions_generated: int = 0


class LiveStateRead(BaseModel):
    """Current scoreboard snapshot from Redis or Postgres."""

    game_pk: int
    home_score: int
    away_score: int
    status: str
    detailed_state: str
    current_inning: Optional[int] = None
    inning_state: Optional[str] = None
    is_top_inning: Optional[bool] = None
    outs: Optional[int] = None
    balls: Optional[int] = None
    strikes: Optional[int] = None
    events_inserted: int = 0
    updated_at: Optional[str] = None
    pitcher_id: Optional[int] = None
    home_win_proba: Optional[float] = None
    away_win_proba: Optional[float] = None
    model_version_id: Optional[int] = None
    model_run_id: Optional[str] = None
    wp_explanation: Optional[str] = None
    wp_delta_home: Optional[float] = None


class LiveSnapshotRead(BaseModel):
    """HTTP envelope matching WebSocket snapshot fields for polling fallback."""

    data: Optional[LiveStateRead] = None
    source: Optional[str] = None
    degraded: bool = False


class GameEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    game_pk: int
    event_id: str
    type: str
    payload: dict[str, Any]
    sequence: int
    ingested_at: datetime


class CalibrationBucket(BaseModel):
    bin_low: float
    bin_high: float
    n: int
    predicted_mean: Optional[float] = None
    actual_rate: Optional[float] = None


class ModelPerformanceRead(BaseModel):
    model_version_id: int
    run_id: str
    n_games: int
    accuracy: Optional[float] = None
    roc_auc: Optional[float] = None
    log_loss: Optional[float] = None
    brier: Optional[float] = None
    calibration_buckets: list[CalibrationBucket] = Field(default_factory=list)
