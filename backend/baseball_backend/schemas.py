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


class NotificationPreferenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    in_app_enabled: bool
    email_enabled: bool
    notify_game_start: bool
    notify_wp_threshold: bool
    notify_high_leverage: bool
    notify_game_final: bool
    notify_new_prediction: bool
    wp_threshold_pct: float


class NotificationPreferenceUpdate(BaseModel):
    in_app_enabled: Optional[bool] = None
    email_enabled: Optional[bool] = None
    notify_game_start: Optional[bool] = None
    notify_wp_threshold: Optional[bool] = None
    notify_high_leverage: Optional[bool] = None
    notify_game_final: Optional[bool] = None
    notify_new_prediction: Optional[bool] = None
    wp_threshold_pct: Optional[float] = Field(default=None, ge=0.01, le=0.99)


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    channel: str
    alert_type: str
    title: str
    body: str
    payload: Optional[dict[str, Any]] = None
    status: str
    read_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    created_at: datetime


class NotificationUnreadCount(BaseModel):
    count: int


class TeamFangraphsStats(BaseModel):
    fangraphs_team: str
    wrc_plus: Optional[float] = None
    team_fip: Optional[float] = None
    bullpen_fip: Optional[float] = None
    median_starter_fip: Optional[float] = None


class PitcherFangraphsStats(BaseModel):
    fangraphs_name: str
    fangraphs_team: Optional[str] = None
    fip: Optional[float] = None
    era: Optional[float] = None
    ip: Optional[float] = None
    gs: Optional[float] = None
    k_per_9: Optional[float] = None
    bb_per_9: Optional[float] = None


class TeamRecordSummary(BaseModel):
    games: int
    wins: int
    losses: int
    runs_scored: int
    runs_allowed: int
    run_diff: int


class VenueSplitSide(BaseModel):
    games: int
    wins: int
    losses: int


class TeamVenueSplits(BaseModel):
    home: VenueSplitSide
    away: VenueSplitSide


class MonthlyTrendRow(BaseModel):
    month: str
    games: int
    wins: int
    losses: int
    runs_scored: int
    runs_allowed: int
    run_diff: int


class TeamPredictionAccuracy(BaseModel):
    n_predictions: int
    n_correct: int
    accuracy: Optional[float] = None
    model_version_id: Optional[int] = None
    run_id: Optional[str] = None


class TeamRecentGame(BaseModel):
    game_pk: int
    game_date: date
    opponent_abbreviation: str
    opponent_name: str
    is_home: bool
    runs_scored: int
    runs_allowed: int
    result: str


class TeamAnalyticsRead(BaseModel):
    team: TeamRead
    season: int
    record: TeamRecordSummary
    splits: TeamVenueSplits
    monthly_trend: list[MonthlyTrendRow]
    prediction_accuracy: TeamPredictionAccuracy
    fangraphs: Optional[TeamFangraphsStats] = None
    recent_games: list[TeamRecentGame] = Field(default_factory=list)


class PlayerDetailRead(BaseModel):
    id: int
    full_name: str
    primary_position: Optional[str] = None
    team: Optional[TeamRead] = None


class PlayerStartGame(BaseModel):
    game_pk: int
    game_date: date
    is_home: bool
    opponent_abbreviation: str
    opponent_name: str
    team_score: Optional[int] = None
    opponent_score: Optional[int] = None
    result: Optional[str] = None
    detailed_state: str


class ProbableStartsSummary(BaseModel):
    games: int
    wins: int
    losses: int
    win_pct: Optional[float] = None
    games_detail: list[PlayerStartGame] = Field(default_factory=list)


class PlayerEventSplits(BaseModel):
    scoring_plays_as_batter: int
    scoring_plays_as_pitcher: int
    rbi: int
    events_scanned: int


class PlayerAnalyticsRead(BaseModel):
    player: PlayerDetailRead
    season: int
    probable_starts: ProbableStartsSummary
    event_splits: PlayerEventSplits
    fangraphs: Optional[PitcherFangraphsStats] = None


class MatchupGameRow(BaseModel):
    game_pk: int
    game_date: date
    venue_home_abbreviation: str
    venue_away_abbreviation: str
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    detailed_state: str
    winner: Optional[str] = None


class HeadToHeadSummary(BaseModel):
    meetings: int
    scored_games: int
    home_wins: int
    away_wins: int
    games: list[MatchupGameRow] = Field(default_factory=list)


class FangraphsDiff(BaseModel):
    wrc_plus: Optional[float] = None
    team_fip: Optional[float] = None
    bullpen_fip: Optional[float] = None
    median_starter_fip: Optional[float] = None


class MatchupAnalyticsRead(BaseModel):
    season: int
    home_team: TeamRead
    away_team: TeamRead
    head_to_head: HeadToHeadSummary
    fangraphs_home: Optional[TeamFangraphsStats] = None
    fangraphs_away: Optional[TeamFangraphsStats] = None
    fangraphs_diff: Optional[FangraphsDiff] = None
