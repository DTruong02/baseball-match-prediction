"""Generate and persist pregame predictions via ``predict_game``."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from baseball_analyze.models.inference import predict_game
from baseball_backend.db.models import Game, Prediction
from baseball_backend.services.model_registry import (
    ModelVersionNotFoundError,
    get_active_pregame_model,
)

_NON_PREDICTABLE_STATES = frozenset(
    {
        "Final",
        "Game Over",
        "Completed Early",
        "Postponed",
        "Cancelled",
    }
)

_PREDICTION_LOAD_OPTIONS = (
    joinedload(Prediction.model_version),
    joinedload(Prediction.game),
)


class GameNotFoundError(LookupError):
    """Raised when a game_pk is not present in the database."""


class PredictionError(ValueError):
    """Raised when inference fails for a game."""


def _format_notes(notes: list[str] | None) -> str | None:
    if not notes:
        return None
    return "\n".join(notes)


def _is_predictable_game(game: Game) -> bool:
    return game.detailed_state not in _NON_PREDICTABLE_STATES


def _upsert_prediction(
    db: Session,
    *,
    game_id: int,
    model_version_id: int,
    home_win_proba: float,
    away_win_proba: float,
    features: dict[str, Any] | None,
    notes: str | None,
) -> Prediction:
    existing = db.scalar(
        select(Prediction).where(
            Prediction.game_id == game_id,
            Prediction.model_version_id == model_version_id,
        )
    )
    if existing is not None:
        existing.home_win_proba = home_win_proba
        existing.away_win_proba = away_win_proba
        existing.features = features
        existing.notes = notes
        return existing

    prediction = Prediction(
        game_id=game_id,
        model_version_id=model_version_id,
        home_win_proba=home_win_proba,
        away_win_proba=away_win_proba,
        features=features,
        notes=notes,
    )
    db.add(prediction)
    return prediction


def _load_prediction(
    db: Session,
    *,
    game_id: int,
    model_version_id: int,
) -> Prediction | None:
    return db.scalar(
        select(Prediction)
        .options(*_PREDICTION_LOAD_OPTIONS)
        .where(
            Prediction.game_id == game_id,
            Prediction.model_version_id == model_version_id,
        )
    )


def generate_prediction_for_game(
    db: Session,
    game: Game,
    *,
    model_version=None,
) -> Prediction:
    """Run inference for ``game`` and upsert a ``Prediction`` row."""
    active_model = model_version or get_active_pregame_model(db)

    existing = _load_prediction(
        db,
        game_id=game.id,
        model_version_id=active_model.id,
    )
    if existing is not None:
        return existing

    if not _is_predictable_game(game):
        raise PredictionError(
            f"Game {game.game_pk} is not predictable (state={game.detailed_state!r})"
        )

    try:
        result = predict_game(game.game_pk, active_model.artifact_path)
    except ValueError as exc:
        raise PredictionError(str(exc)) from exc

    prediction = _upsert_prediction(
        db,
        game_id=game.id,
        model_version_id=active_model.id,
        home_win_proba=float(result["home_win_proba"]),
        away_win_proba=float(result["away_win_proba"]),
        features=result.get("features"),
        notes=_format_notes(result.get("notes")),
    )
    db.commit()

    loaded = _load_prediction(
        db,
        game_id=game.id,
        model_version_id=active_model.id,
    )
    if loaded is None:
        raise PredictionError(f"Failed to persist prediction for game {game.game_pk}")
    return loaded


def get_prediction_for_game_pk(db: Session, game_pk: int) -> Prediction | None:
    """
    Return a stored prediction for ``game_pk``, generating one when missing.

    Returns ``None`` when no active pregame model is registered, the game is not
    predictable, or inference cannot run.
    """
    game = db.scalar(select(Game).where(Game.game_pk == game_pk))
    if game is None:
        raise GameNotFoundError(f"Game not found: game_pk={game_pk}")

    try:
        model_version = get_active_pregame_model(db)
    except ModelVersionNotFoundError:
        return None

    existing = _load_prediction(
        db,
        game_id=game.id,
        model_version_id=model_version.id,
    )
    if existing is not None:
        return existing

    if not _is_predictable_game(game):
        return None

    try:
        return generate_prediction_for_game(db, game, model_version=model_version)
    except PredictionError:
        return None


def generate_missing_predictions_for_date(
    db: Session,
    game_date: str | date,
) -> int:
    """
    Generate predictions for predictable games on ``game_date`` that lack one.

    Returns the number of new predictions created.
    """
    if isinstance(game_date, date):
        target_date = game_date
    else:
        target_date = date.fromisoformat(game_date)

    try:
        model_version = get_active_pregame_model(db)
    except ModelVersionNotFoundError:
        return 0

    games = db.scalars(select(Game).where(Game.game_date == target_date).order_by(Game.game_pk)).all()
    created = 0

    for game in games:
        if not _is_predictable_game(game):
            continue

        existing = db.scalar(
            select(Prediction.id).where(
                Prediction.game_id == game.id,
                Prediction.model_version_id == model_version.id,
            )
        )
        if existing is not None:
            continue

        try:
            generate_prediction_for_game(db, game, model_version=model_version)
        except PredictionError:
            continue
        created += 1

    return created
