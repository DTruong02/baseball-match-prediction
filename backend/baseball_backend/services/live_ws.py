"""WebSocket connection fan-out for live game state updates."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.orm import Session

from baseball_backend.db.models import Game
from baseball_backend.redis_client import get_redis_client
from baseball_backend.services.live_cache import (
    LIVE_UPDATE_CHANNEL_PATTERN,
    get_cached_live_state,
    parse_game_pk_from_channel,
)
from baseball_backend.settings import get_settings

logger = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 5.0


def envelope(
    msg_type: str,
    *,
    game_pk: int | None = None,
    data: Any = None,
    source: str | None = None,
    degraded: bool = False,
    date: str | None = None,
) -> dict[str, Any]:
    """Build a client-facing WebSocket message."""
    payload: dict[str, Any] = {
        "type": msg_type,
        "data": data,
        "source": source,
        "degraded": degraded,
    }
    if game_pk is not None:
        payload["game_pk"] = game_pk
    if date is not None:
        payload["date"] = date
    return payload


def postgres_live_snapshot(db: Session, game_pk: int) -> dict[str, Any] | None:
    """Build a degraded scoreboard snapshot from the last Postgres game row."""
    game = db.scalar(select(Game).where(Game.game_pk == game_pk))
    if game is None:
        return None
    updated_at = game.updated_at
    if isinstance(updated_at, datetime) and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return {
        "game_pk": game.game_pk,
        "home_score": game.home_score if game.home_score is not None else 0,
        "away_score": game.away_score if game.away_score is not None else 0,
        "status": game.status,
        "detailed_state": game.detailed_state,
        "current_inning": None,
        "inning_state": None,
        "is_top_inning": None,
        "outs": None,
        "balls": None,
        "strikes": None,
        "events_inserted": 0,
        "updated_at": updated_at.isoformat() if updated_at is not None else None,
    }


def resolve_live_snapshot(
    db: Session,
    game_pk: int,
) -> tuple[dict[str, Any] | None, str | None, bool]:
    """
    Prefer Redis live state; fall back to Postgres.

    Returns ``(payload, source, degraded)``.
    """
    cached = get_cached_live_state(game_pk)
    if cached is not None:
        return cached, "redis", False

    snapshot = postgres_live_snapshot(db, game_pk)
    if snapshot is not None:
        return snapshot, "postgres", True

    return None, None, True


def list_game_pks_for_date(db: Session, game_date: date) -> list[int]:
    rows = db.scalars(
        select(Game.game_pk).where(Game.game_date == game_date).order_by(Game.game_pk)
    ).all()
    return list(rows)


class LiveConnectionManager:
    """Track WebSocket subscribers and broadcast live updates."""

    def __init__(self) -> None:
        self._game_sockets: dict[int, set[WebSocket]] = defaultdict(set)
        self._slate_sockets: dict[str, set[WebSocket]] = defaultdict(set)
        self._slate_game_pks: dict[WebSocket, set[int]] = {}
        self._lock = asyncio.Lock()

    async def connect_game(self, websocket: WebSocket, game_pk: int) -> None:
        await websocket.accept()
        async with self._lock:
            self._game_sockets[game_pk].add(websocket)

    async def connect_slate(
        self,
        websocket: WebSocket,
        game_date: str,
        game_pks: set[int],
    ) -> None:
        await websocket.accept()
        async with self._lock:
            self._slate_sockets[game_date].add(websocket)
            self._slate_game_pks[websocket] = set(game_pks)

    async def disconnect(self, websocket: WebSocket, *, game_pk: int | None = None) -> None:
        async with self._lock:
            if game_pk is not None:
                sockets = self._game_sockets.get(game_pk)
                if sockets is not None:
                    sockets.discard(websocket)
                    if not sockets:
                        del self._game_sockets[game_pk]

            for date_key, sockets in list(self._slate_sockets.items()):
                if websocket in sockets:
                    sockets.discard(websocket)
                    if not sockets:
                        del self._slate_sockets[date_key]
            self._slate_game_pks.pop(websocket, None)

    async def update_slate_games(self, websocket: WebSocket, game_pks: set[int]) -> None:
        async with self._lock:
            if websocket in self._slate_game_pks:
                self._slate_game_pks[websocket] = set(game_pks)

    async def broadcast_game_update(self, game_pk: int, message: dict[str, Any]) -> None:
        recipients: list[WebSocket] = []
        async with self._lock:
            recipients.extend(self._game_sockets.get(game_pk, ()))
            for websocket, interested in self._slate_game_pks.items():
                if game_pk in interested:
                    recipients.append(websocket)

        for websocket in recipients:
            try:
                await websocket.send_json(message)
            except Exception:
                logger.debug("Failed to send live update to websocket", exc_info=True)


# Process-wide manager used by routes and the Redis fan-out task.
connection_manager = LiveConnectionManager()

_fanout_stop_event: asyncio.Event | None = None
_fanout_task: asyncio.Task[None] | None = None
_fanout_lock = asyncio.Lock()


def configure_live_fanout(stop_event: asyncio.Event) -> None:
    """Register the lifespan stop event used to shut down Redis fan-out."""
    global _fanout_stop_event, _fanout_task
    _fanout_stop_event = stop_event
    _fanout_task = None


async def ensure_live_fanout_started() -> None:
    """Lazily start Redis pub/sub fan-out when the first WebSocket connects."""
    global _fanout_task
    settings = get_settings()
    if not settings.redis_enabled or not settings.live_pubsub_enabled:
        return
    if _fanout_stop_event is None:
        return

    async with _fanout_lock:
        if _fanout_task is not None and not _fanout_task.done():
            return
        _fanout_task = asyncio.create_task(
            run_live_pubsub_fanout(stop_event=_fanout_stop_event),
            name="live-pubsub-fanout",
        )


async def shutdown_live_fanout() -> None:
    """Cancel the Redis fan-out task during app shutdown."""
    global _fanout_task
    if _fanout_stop_event is not None:
        _fanout_stop_event.set()
    task = _fanout_task
    _fanout_task = None
    if task is None:
        return
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass


async def run_live_pubsub_fanout(
    manager: LiveConnectionManager | None = None,
    *,
    stop_event: asyncio.Event | None = None,
) -> None:
    """
    Subscribe to Redis live update channels and fan out to local WebSockets.

    No-ops when Redis or pub/sub is disabled. Started lazily on the first
    WebSocket connection (see ``ensure_live_fanout_started``).
    """
    manager = manager or connection_manager
    settings = get_settings()
    if not settings.redis_enabled or not settings.live_pubsub_enabled:
        logger.info("Live WebSocket Redis fan-out disabled")
        return

    client = get_redis_client()
    if client is None:
        logger.warning("Live WebSocket Redis fan-out skipped; Redis client unavailable")
        return

    pubsub = client.pubsub(ignore_subscribe_messages=True)
    try:
        await asyncio.to_thread(pubsub.psubscribe, LIVE_UPDATE_CHANNEL_PATTERN)
        logger.info(
            "Live WebSocket Redis fan-out subscribed to %s",
            LIVE_UPDATE_CHANNEL_PATTERN,
        )
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            message = await asyncio.to_thread(pubsub.get_message, timeout=1.0)
            if message is None:
                await asyncio.sleep(0)
                continue
            if message.get("type") != "pmessage":
                continue

            channel = message.get("channel")
            if not isinstance(channel, str):
                continue
            game_pk = parse_game_pk_from_channel(channel)
            if game_pk is None:
                continue

            raw = message.get("data")
            if not isinstance(raw, str):
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Invalid live update JSON on channel %s", channel)
                continue

            await manager.broadcast_game_update(
                game_pk,
                envelope("update", game_pk=game_pk, data=data, source="redis"),
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Live WebSocket Redis fan-out stopped unexpectedly")
    finally:
        try:
            await asyncio.to_thread(pubsub.punsubscribe, LIVE_UPDATE_CHANNEL_PATTERN)
            await asyncio.to_thread(pubsub.close)
        except Exception:
            logger.debug("Error closing live pubsub", exc_info=True)


async def poll_live_state_updates(
    websocket: WebSocket,
    game_pks: set[int],
    *,
    interval_seconds: float = _POLL_INTERVAL_SECONDS,
    stop_event: asyncio.Event | None = None,
) -> None:
    """
    Poll Redis cache and push changes when pub/sub is unavailable.

    Used as a per-connection fallback so clients still get updates if
    ``LIVE_PUBSUB_ENABLED`` is false but the cache is being written.
    """
    last_payloads: dict[int, str] = {}
    while True:
        if stop_event is not None and stop_event.is_set():
            return
        for game_pk in list(game_pks):
            data = get_cached_live_state(game_pk)
            if data is None:
                continue
            encoded = json.dumps(data, sort_keys=True)
            if last_payloads.get(game_pk) == encoded:
                continue
            last_payloads[game_pk] = encoded
            try:
                await websocket.send_json(
                    envelope("update", game_pk=game_pk, data=data, source="redis")
                )
            except Exception:
                return
        await asyncio.sleep(interval_seconds)
