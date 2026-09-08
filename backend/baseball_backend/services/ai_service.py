"""Grounded AI explain / summarize / Q&A backed by chat tools."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from baseball_analyze.chat_repl import (
    EXPLAIN_SYSTEM_PROMPT,
    SUMMARIZE_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    GroundedChatResult,
    LLMClientConfig,
    get_client_config,
    run_grounded_chat,
)
from baseball_backend.db.models import Game, GameEvent, Prediction
from baseball_backend.services.live_prediction_service import get_game_win_probabilities
from baseball_backend.services.model_registry import (
    ModelVersionNotFoundError,
    get_active_pregame_model,
)
from baseball_backend.services.prediction_service import (
    GameNotFoundError,
    get_prediction_for_game_pk,
)
from baseball_backend.settings import Settings

logger = logging.getLogger(__name__)


class AiConfigError(RuntimeError):
    """Raised when the LLM client cannot be configured or imported."""


class AiPredictionUnavailableError(LookupError):
    """Raised when a game has no stored/generatable pregame prediction."""


def resolve_llm_config(settings: Settings) -> LLMClientConfig:
    """Build LLM client config from backend settings / environment."""
    try:
        return get_client_config(
            base_url=settings.llm_base_url or None,
            api_key=settings.llm_api_key or None,
            model=settings.llm_model or None,
        )
    except RuntimeError as exc:
        raise AiConfigError(str(exc)) from exc


def _active_model_path(db: Session) -> Path:
    try:
        model = get_active_pregame_model(db)
    except ModelVersionNotFoundError as exc:
        raise AiConfigError(
            "No active pregame model is registered; register one before using AI Q&A."
        ) from exc
    return Path(model.artifact_path)


def _require_game(db: Session, game_pk: int) -> Game:
    game = db.scalar(
        select(Game)
        .where(Game.game_pk == game_pk)
        .options(
            joinedload(Game.home_team),
            joinedload(Game.away_team),
            joinedload(Game.home_probable_pitcher),
            joinedload(Game.away_probable_pitcher),
        )
    )
    if game is None:
        raise GameNotFoundError(f"Game not found: game_pk={game_pk}")
    return game


def _team_payload(team: Any) -> dict[str, Any]:
    return {
        "id": team.id,
        "abbreviation": team.abbreviation,
        "name": team.name,
        "city": team.city,
    }


def _prediction_payload(prediction: Prediction | None) -> dict[str, Any] | None:
    if prediction is None:
        return None
    model_version = prediction.model_version
    return {
        "home_win_proba": prediction.home_win_proba,
        "away_win_proba": prediction.away_win_proba,
        "features": prediction.features,
        "notes": prediction.notes,
        "model_version": {
            "id": model_version.id,
            "run_id": model_version.run_id,
        },
        "created_at": prediction.created_at.isoformat()
        if prediction.created_at
        else None,
    }


def _game_payload(game: Game) -> dict[str, Any]:
    return {
        "game_pk": game.game_pk,
        "game_date": game.game_date.isoformat(),
        "season": game.season,
        "status": game.status,
        "detailed_state": game.detailed_state,
        "home_team": _team_payload(game.home_team),
        "away_team": _team_payload(game.away_team),
        "venue_name": game.venue_name,
        "home_score": game.home_score,
        "away_score": game.away_score,
        "winner": game.winner,
        "home_probable_pitcher": (
            game.home_probable_pitcher.full_name if game.home_probable_pitcher else None
        ),
        "away_probable_pitcher": (
            game.away_probable_pitcher.full_name if game.away_probable_pitcher else None
        ),
        "live_state": game.live_state if isinstance(game.live_state, dict) else None,
    }


def _recent_events(db: Session, game_pk: int, *, limit: int = 12) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(GameEvent)
        .where(GameEvent.game_pk == game_pk)
        .order_by(GameEvent.sequence.desc())
        .limit(limit)
    ).all()
    out: list[dict[str, Any]] = []
    for row in reversed(rows):
        out.append(
            {
                "event_id": row.event_id,
                "type": row.type,
                "sequence": row.sequence,
                "payload": row.payload,
            }
        )
    return out


def _run_context_completion(
    *,
    system_prompt: str,
    grounded_context: dict[str, Any],
    user_instruction: str,
    settings: Settings,
) -> str:
    config = resolve_llm_config(settings)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"{user_instruction}\n\n"
                "Grounded JSON context (use only these values):\n"
                f"{json.dumps(grounded_context, default=str)}"
            ),
        },
    ]
    try:
        result = run_grounded_chat(
            messages,
            config=config,
            tools=[],
            dispatch={},
            max_tool_rounds=1,
        )
    except RuntimeError as exc:
        raise AiConfigError(str(exc)) from exc
    except Exception as exc:  # pragma: no cover - provider errors
        logger.exception("Grounded AI completion failed")
        raise AiConfigError(f"LLM request failed: {exc}") from exc
    return result.answer


def explain_model_lean(
    db: Session,
    game_pk: int,
    settings: Settings,
) -> dict[str, Any]:
    """Narrate the stored pregame lean using prediction features only."""
    game = _require_game(db, game_pk)
    prediction = get_prediction_for_game_pk(db, game_pk)
    if prediction is None:
        raise AiPredictionUnavailableError(
            f"No pregame prediction available for game_pk={game_pk}"
        )

    # Ensure relationship is loaded for payload.
    _ = prediction.model_version
    _ = prediction.game

    context = {
        "game": _game_payload(game),
        "pregame_prediction": _prediction_payload(prediction),
    }
    explanation = _run_context_completion(
        system_prompt=EXPLAIN_SYSTEM_PROMPT,
        grounded_context=context,
        user_instruction=(
            "Explain why the model leans home or away for this game. "
            "Reference signed feature diffs when present."
        ),
        settings=settings,
    )
    return {
        "game_pk": game_pk,
        "explanation": explanation,
        "home_win_proba": prediction.home_win_proba,
        "away_win_proba": prediction.away_win_proba,
        "features": prediction.features,
        "notes": prediction.notes,
        "model_version": {
            "id": prediction.model_version.id,
            "run_id": prediction.model_version.run_id,
        },
    }


def summarize_game(
    db: Session,
    game_pk: int,
    settings: Settings,
) -> dict[str, Any]:
    """Summarize a game from stored schedule, predictions, live state, and events."""
    game = _require_game(db, game_pk)
    # Prefer generating a missing pregame line when possible (same as game detail).
    get_prediction_for_game_pk(db, game_pk)
    pregame, live = get_game_win_probabilities(db, game_pk)

    context = {
        "game": _game_payload(game),
        "pregame_prediction": _prediction_payload(pregame),
        "live_prediction": _prediction_payload(live),
        "recent_events": _recent_events(db, game_pk),
    }
    summary = _run_context_completion(
        system_prompt=SUMMARIZE_SYSTEM_PROMPT,
        grounded_context=context,
        user_instruction="Write a concise grounded summary of this game.",
        settings=settings,
    )
    return {
        "game_pk": game_pk,
        "summary": summary,
    }


def ask_question(
    db: Session,
    *,
    question: str,
    settings: Settings,
    game_pk: int | None = None,
    date: str | None = None,
) -> dict[str, Any]:
    """
    Natural-language Q&A using the grounded chat tool loop.

    Probabilities come only from ``predict_games`` / stored tools — never invented.
    """
    text = (question or "").strip()
    if not text:
        raise ValueError("question must be a non-empty string")

    config = resolve_llm_config(settings)
    model_path = _active_model_path(db)

    focus_bits: list[str] = []
    if game_pk is not None:
        focus_bits.append(f"Focus game_pk={game_pk} when relevant.")
        try:
            game = _require_game(db, game_pk)
            focus_bits.append(
                "Known game context (may be incomplete; still call tools for probs): "
                + json.dumps(_game_payload(game), default=str)
            )
        except GameNotFoundError:
            focus_bits.append(
                f"game_pk={game_pk} was requested but is not in the database; "
                "use schedule tools if needed."
            )
    if date:
        focus_bits.append(f"Preferred date hint: {date}")

    user_content = text
    if focus_bits:
        user_content = text + "\n\n" + "\n".join(focus_bits)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        result: GroundedChatResult = run_grounded_chat(
            messages,
            model_path=model_path,
            cache_dir=None,
            config=config,
        )
    except RuntimeError as exc:
        raise AiConfigError(str(exc)) from exc
    except Exception as exc:  # pragma: no cover - provider errors
        logger.exception("Grounded AI ask failed")
        raise AiConfigError(f"LLM request failed: {exc}") from exc

    return {
        "answer": result.answer,
        "tool_trace": result.tool_trace,
        "game_pk": game_pk,
        "date": date,
    }
