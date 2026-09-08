"""Grounded AI routes: explain lean, summarize game, NL Q&A."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from baseball_backend.db.models import User
from baseball_backend.db.session import get_db
from baseball_backend.deps import get_current_user
from baseball_backend.schemas import (
    AiAskRequest,
    AiAskResponse,
    AiExplainRequest,
    AiExplainResponse,
    AiSummarizeRequest,
    AiSummarizeResponse,
    ModelVersionSummary,
)
from baseball_backend.services.ai_service import (
    AiConfigError,
    AiPredictionUnavailableError,
    ask_question,
    explain_model_lean,
    summarize_game,
)
from baseball_backend.services.prediction_service import GameNotFoundError
from baseball_backend.settings import Settings, get_settings

router = APIRouter(prefix="/ai", tags=["ai"])


def _map_ai_error(exc: Exception) -> HTTPException:
    if isinstance(exc, GameNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, AiPredictionUnavailableError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, AiConfigError):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )
    if isinstance(exc, ValueError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="AI request failed",
    )


@router.post("/explain", response_model=AiExplainResponse)
def post_explain(
    body: AiExplainRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _current_user: User = Depends(get_current_user),
) -> AiExplainResponse:
    try:
        payload = explain_model_lean(db, body.game_pk, settings)
    except (GameNotFoundError, AiPredictionUnavailableError, AiConfigError, ValueError) as exc:
        raise _map_ai_error(exc) from exc
    return AiExplainResponse(
        game_pk=payload["game_pk"],
        explanation=payload["explanation"],
        home_win_proba=payload["home_win_proba"],
        away_win_proba=payload["away_win_proba"],
        features=payload.get("features"),
        notes=payload.get("notes"),
        model_version=ModelVersionSummary.model_validate(payload["model_version"]),
    )


@router.post("/summarize-game", response_model=AiSummarizeResponse)
def post_summarize_game(
    body: AiSummarizeRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _current_user: User = Depends(get_current_user),
) -> AiSummarizeResponse:
    try:
        payload = summarize_game(db, body.game_pk, settings)
    except (GameNotFoundError, AiConfigError, ValueError) as exc:
        raise _map_ai_error(exc) from exc
    return AiSummarizeResponse(
        game_pk=payload["game_pk"],
        summary=payload["summary"],
    )


@router.post("/ask", response_model=AiAskResponse)
def post_ask(
    body: AiAskRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _current_user: User = Depends(get_current_user),
) -> AiAskResponse:
    try:
        payload = ask_question(
            db,
            question=body.question,
            settings=settings,
            game_pk=body.game_pk,
            date=body.date,
        )
    except (AiConfigError, ValueError) as exc:
        raise _map_ai_error(exc) from exc
    return AiAskResponse(
        answer=payload["answer"],
        tool_trace=payload.get("tool_trace") or [],
        game_pk=payload.get("game_pk"),
        date=payload.get("date"),
    )
