"""Live (in-game) win-probability inference on meaningful state changes."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from baseball_analyze.features.in_game import state_from_linescore
from baseball_analyze.models.inference import predict_in_game
from baseball_backend.db.models import Game, Prediction
from baseball_backend.services.live_normalize import GameLiveState, NormalizedGameEvent
from baseball_backend.services.model_registry import (
    ModelVersionNotFoundError,
    get_active_in_game_model,
)
from baseball_backend.services.prediction_service import (
    PredictionError,
    _format_notes,
    _load_prediction,
    _upsert_prediction,
)

logger = logging.getLogger(__name__)

_OUT_EVENT_TOKENS = (
    "out",
    "strikeout",
    "double_play",
    "triple_play",
    "sac_fly",
    "sac_bunt",
    "fielders_choice",
    "caught_stealing",
    "pickoff",
)

_PITCHING_CHANGE_TOKENS = (
    "pitching_substitution",
    "pitcher_change",
    "defensive_sub",
)


def is_meaningful_event(payload: dict[str, Any]) -> bool:
    """
    Return True for plays that should trigger live WP re-inference.

    Meaningful: scoring plays, outs, and pitching changes. Incomplete mid-PA
    updates (balls/strikes only) are ignored.
    """
    if payload.get("is_scoring_play"):
        return True
    rbi = payload.get("rbi")
    if isinstance(rbi, (int, float)) and rbi > 0:
        return True

    event_type = str(payload.get("event_type") or "").lower()
    event_name = str(payload.get("event") or "").lower()
    combined = f"{event_type} {event_name}"

    if any(token in combined for token in _PITCHING_CHANGE_TOKENS):
        return True
    if any(token in event_type for token in _OUT_EVENT_TOKENS):
        return True
    if "out" in event_name and payload.get("is_complete"):
        return True

    return False


def extract_pitcher_id(live_feed: dict[str, Any]) -> int | None:
    """Current defending pitcher id from linescore / current play."""
    state = state_from_linescore(live_feed)
    if state is not None and state.pitcher_id is not None:
        return int(state.pitcher_id)
    return None


def should_run_live_inference(
    *,
    previous_state: GameLiveState | None,
    new_state: GameLiveState,
    new_events: list[NormalizedGameEvent],
    previous_pitcher_id: int | None,
    current_pitcher_id: int | None,
    has_existing_prediction: bool,
) -> bool:
    """
    Decide whether to re-run the in-game model for this sync tick.

    Triggers on newly ingested meaningful plays, score/outs/inning changes,
    pitching changes, or the first live prediction for a game.
    """
    if not has_existing_prediction:
        # First live WP once the game has started (or we have play state).
        if new_state.status in {"Live", "Final"} or new_state.current_inning is not None:
            return True

    for event in new_events:
        if is_meaningful_event(event.payload or {}):
            return True

    if previous_state is None:
        return False

    if (
        previous_state.home_score != new_state.home_score
        or previous_state.away_score != new_state.away_score
    ):
        return True
    if previous_state.outs != new_state.outs:
        return True
    if (
        previous_state.current_inning != new_state.current_inning
        or previous_state.is_top_inning != new_state.is_top_inning
    ):
        return True
    if (
        previous_pitcher_id is not None
        and current_pitcher_id is not None
        and previous_pitcher_id != current_pitcher_id
    ):
        return True

    return False


def generate_live_prediction_for_game(
    db: Session,
    game: Game,
    live_feed: dict[str, Any],
    *,
    model_version=None,
) -> Prediction | None:
    """
    Run in-game inference and upsert the live ``Prediction`` row.

    Returns ``None`` when no active in-game model is registered or features
    cannot be built. Does not commit; caller owns the transaction.
    """
    try:
        active_model = model_version or get_active_in_game_model(db)
    except ModelVersionNotFoundError:
        return None

    home = game.home_team
    away = game.away_team
    if home is None or away is None:
        raise PredictionError(f"Game {game.game_pk} is missing team relationships")

    try:
        result = predict_in_game(
            live_feed,
            active_model.artifact_path,
            game_pk=game.game_pk,
            season=game.season,
            home_abbrev=home.abbreviation,
            away_abbrev=away.abbreviation,
        )
    except ValueError as exc:
        logger.debug("Live inference skipped for game_pk=%s: %s", game.game_pk, exc)
        return None

    prediction = _upsert_prediction(
        db,
        game_id=game.id,
        model_version_id=active_model.id,
        home_win_proba=float(result["home_win_proba"]),
        away_win_proba=float(result["away_win_proba"]),
        features=result.get("features"),
        notes=_format_notes(result.get("notes")),
    )
    prediction.model_version = active_model
    db.flush()
    return prediction


def get_live_prediction_for_game_pk(db: Session, game_pk: int) -> Prediction | None:
    """Return the stored live WP prediction for ``game_pk``, if any."""
    game = db.scalar(select(Game).where(Game.game_pk == game_pk))
    if game is None:
        return None

    try:
        model_version = get_active_in_game_model(db)
    except ModelVersionNotFoundError:
        return None

    return _load_prediction(
        db,
        game_id=game.id,
        model_version_id=model_version.id,
    )


def get_game_win_probabilities(
    db: Session,
    game_pk: int,
) -> tuple[Prediction | None, Prediction | None]:
    """
    Return ``(pregame_prediction, live_prediction)`` for the active models.

    Missing active models or rows yield ``None`` for that slot. Does not
    generate new predictions.
    """
    from baseball_backend.services.model_registry import get_active_pregame_model

    game = db.scalar(select(Game).where(Game.game_pk == game_pk))
    if game is None:
        return None, None

    pregame: Prediction | None = None
    try:
        pregame_model = get_active_pregame_model(db)
        pregame = _load_prediction(
            db,
            game_id=game.id,
            model_version_id=pregame_model.id,
        )
    except ModelVersionNotFoundError:
        pregame = None

    live = get_live_prediction_for_game_pk(db, game_pk)
    return pregame, live


def win_probability_payload(prediction: Prediction | None) -> dict[str, Any] | None:
    """Compact WP fields for live Redis / WebSocket snapshots."""
    if prediction is None:
        return None
    if prediction.home_win_proba is None or prediction.away_win_proba is None:
        return None
    model = prediction.model_version
    payload: dict[str, Any] = {
        "home_win_proba": float(prediction.home_win_proba),
        "away_win_proba": float(prediction.away_win_proba),
    }
    if model is not None:
        payload["model_version_id"] = model.id
        payload["model_run_id"] = model.run_id
    return payload
