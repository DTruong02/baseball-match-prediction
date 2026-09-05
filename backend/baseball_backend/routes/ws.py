"""WebSocket endpoints for live game state."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from baseball_backend.db.models import User
from baseball_backend.db.session import get_session_factory
from baseball_backend.security import decode_access_token
from baseball_backend.services.live_ws import (
    connection_manager,
    envelope,
    ensure_live_fanout_started,
    list_game_pks_for_date,
    poll_live_state_updates,
    resolve_live_snapshot,
)
from baseball_backend.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["live-ws"])


@contextmanager
def db_session_scope() -> Iterator[Session]:
    """Yield a SQLAlchemy session (patchable in tests)."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def _authenticate_token(token: Optional[str]) -> Optional[str]:
    """Validate JWT and return the subject email, or ``None`` if invalid."""
    if not token:
        return None
    settings = get_settings()
    try:
        payload = decode_access_token(token, settings.secret_key)
    except ValueError:
        return None

    try:
        with db_session_scope() as db:
            user = db.scalar(select(User).where(User.email == payload.sub))
            if user is None:
                return None
            return payload.sub
    except Exception:
        logger.exception("WebSocket auth DB lookup failed")
        return None


async def _reject_unauthorized(websocket: WebSocket) -> None:
    await websocket.close(code=1008, reason="Unauthorized")


@router.websocket("/ws/games/{game_pk}")
async def websocket_game_live(
    websocket: WebSocket,
    game_pk: int,
    token: Optional[str] = Query(default=None),
) -> None:
    """
    Stream live scoreboard state for a single game.

    Clients should pass a JWT as ``?token=`` (browsers cannot set Authorization
    on the WebSocket handshake). On connect, a snapshot is sent from Redis or
    Postgres; subsequent Redis pub/sub (or cache poll) updates are forwarded.
    """
    if _authenticate_token(token) is None:
        await _reject_unauthorized(websocket)
        return

    await connection_manager.connect_game(websocket, game_pk)
    await ensure_live_fanout_started()
    stop_event = asyncio.Event()
    poll_task: asyncio.Task[None] | None = None

    try:
        with db_session_scope() as db:
            data, source, degraded = resolve_live_snapshot(db, game_pk)

        await websocket.send_json(
            envelope(
                "snapshot",
                game_pk=game_pk,
                data=data,
                source=source,
                degraded=degraded,
            )
        )

        settings = get_settings()
        if settings.redis_enabled and not settings.live_pubsub_enabled:
            poll_task = asyncio.create_task(
                poll_live_state_updates(
                    websocket,
                    {game_pk},
                    stop_event=stop_event,
                )
            )

        while True:
            # Keep the connection open; clients may send pings or noop text.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket error for game_pk=%s", game_pk)
    finally:
        stop_event.set()
        if poll_task is not None:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
        await connection_manager.disconnect(websocket, game_pk=game_pk)


def _parse_date_param(raw: Optional[str]) -> date:
    if not raw:
        return date.today()
    return datetime.strptime(raw, "%Y-%m-%d").date()


@router.websocket("/ws/live")
async def websocket_live_slate(
    websocket: WebSocket,
    date: Optional[str] = Query(default=None),
    token: Optional[str] = Query(default=None),
) -> None:
    """
    Stream live scoreboard updates for all games on a date slate.

    Query params: ``date=YYYY-MM-DD`` (defaults to today) and ``token``.
    """
    if _authenticate_token(token) is None:
        await _reject_unauthorized(websocket)
        return

    try:
        game_date = _parse_date_param(date)
    except ValueError:
        await websocket.close(code=1008, reason="Invalid date")
        return

    date_key = game_date.isoformat()
    with db_session_scope() as db:
        game_pks = list_game_pks_for_date(db, game_date)
        snapshots = []
        for game_pk in game_pks:
            data, source, degraded = resolve_live_snapshot(db, game_pk)
            snapshots.append(
                envelope(
                    "snapshot",
                    game_pk=game_pk,
                    data=data,
                    source=source,
                    degraded=degraded,
                )
            )

    await connection_manager.connect_slate(websocket, date_key, set(game_pks))
    await ensure_live_fanout_started()
    stop_event = asyncio.Event()
    poll_task: asyncio.Task[None] | None = None

    try:
        await websocket.send_json(
            envelope(
                "slate_snapshot",
                date=date_key,
                data=snapshots,
            )
        )

        settings = get_settings()
        if settings.redis_enabled and not settings.live_pubsub_enabled:
            poll_task = asyncio.create_task(
                poll_live_state_updates(
                    websocket,
                    set(game_pks),
                    stop_event=stop_event,
                )
            )

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket error for live slate date=%s", date_key)
    finally:
        stop_event.set()
        if poll_task is not None:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass
        await connection_manager.disconnect(websocket)
