"""Poll MLB live feeds and persist game state + play events."""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from baseball_analyze.data.mlb_client import (
    MLBAPIError,
    ScheduledGame,
    fetch_live_feed,
    fetch_schedule_for_date,
)
from baseball_backend.db.models import Game, GameEvent
from baseball_backend.services.live_cache import (
    cache_live_state,
    serialize_live_state,
    set_live_feed_health,
)
from baseball_backend.services.live_normalize import (
    GameLiveState,
    NormalizedGameEvent,
    normalize_game_state,
    normalize_play_events,
)
from baseball_backend.services.live_prediction_service import (
    extract_pitcher_id,
    generate_live_prediction_for_game,
    get_live_prediction_for_game_pk,
    should_run_live_inference,
    win_probability_payload,
)
from baseball_backend.services.live_rate_limit import PollRateLimiter
from baseball_backend.services.live_status import is_live_tracking_status
from baseball_backend.services.live_wp_explanations import build_wp_swing_explanation
from baseball_backend.services.schedule_sync import _FINAL_STATES
from baseball_backend.settings import get_settings

logger = logging.getLogger(__name__)


def _winner_from_scores(
    home_score: int,
    away_score: int,
    home_abbrev: str,
    away_abbrev: str,
) -> str | None:
    if home_score == away_score:
        return None
    return home_abbrev if home_score > away_score else away_abbrev


def _apply_live_state(game: Game, state: GameLiveState) -> None:
    game.status = state.status
    game.detailed_state = state.detailed_state
    game.home_score = state.home_score
    game.away_score = state.away_score

    if state.detailed_state in _FINAL_STATES:
        home_abbrev = game.home_team.abbreviation if game.home_team else None
        away_abbrev = game.away_team.abbreviation if game.away_team else None
        if home_abbrev and away_abbrev:
            game.winner = _winner_from_scores(
                state.home_score,
                state.away_score,
                home_abbrev,
                away_abbrev,
            )


def _existing_events_by_id(db: Session, game_pk: int) -> dict[str, GameEvent]:
    rows = db.scalars(select(GameEvent).where(GameEvent.game_pk == game_pk)).all()
    return {row.event_id: row for row in rows}


def _upsert_events(
    db: Session,
    game_pk: int,
    events: list[NormalizedGameEvent],
    existing_by_id: dict[str, GameEvent],
) -> tuple[int, list[NormalizedGameEvent]]:
    """
    Insert new play events and refresh payloads for known ids.

    MLB often exposes an at-bat before ``result.description`` is filled. We
    insert on first sight, then overwrite the payload when the feed catches up.

    Uses a savepoint per insert so concurrent workers racing on the same
    ``(game_pk, event_id)`` unique constraint do not abort the whole sync.

    Returns ``(inserted_count, changed_events)`` where changed includes both
    inserts and payload updates (for live WP / explanation triggers).
    """
    inserted = 0
    changed_events: list[NormalizedGameEvent] = []
    for event in events:
        existing = existing_by_id.get(event.event_id)
        if existing is not None:
            if existing.payload != event.payload:
                existing.payload = dict(event.payload)
                existing.type = event.type
                changed_events.append(event)
            continue
        try:
            with db.begin_nested():
                row = GameEvent(
                    game_pk=game_pk,
                    event_id=event.event_id,
                    type=event.type,
                    payload=event.payload,
                    sequence=event.sequence,
                )
                db.add(row)
                db.flush()
        except IntegrityError:
            logger.debug(
                "Skipping duplicate event game_pk=%s event_id=%s",
                game_pk,
                event.event_id,
            )
            # Another worker won the race; reload is unnecessary for this tick.
            continue
        existing_by_id[event.event_id] = row
        inserted += 1
        changed_events.append(event)
    return inserted, changed_events


def _previous_live_state(game: Game) -> GameLiveState | None:
    snapshot = game.live_state
    if not isinstance(snapshot, dict):
        return None
    try:
        return GameLiveState(
            home_score=int(snapshot.get("home_score") or 0),
            away_score=int(snapshot.get("away_score") or 0),
            status=str(snapshot.get("status") or "Unknown"),
            detailed_state=str(snapshot.get("detailed_state") or "Unknown"),
            current_inning=snapshot.get("current_inning"),
            inning_state=snapshot.get("inning_state"),
            is_top_inning=snapshot.get("is_top_inning"),
            outs=snapshot.get("outs"),
            balls=snapshot.get("balls"),
            strikes=snapshot.get("strikes"),
            on_1b=snapshot.get("on_1b"),
            on_2b=snapshot.get("on_2b"),
            on_3b=snapshot.get("on_3b"),
        )
    except (TypeError, ValueError):
        return None


def _wp_fields_from_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not snapshot:
        return {}
    fields: dict[str, Any] = {}
    for key in (
        "home_win_proba",
        "away_win_proba",
        "model_version_id",
        "model_run_id",
        "pitcher_id",
        "wp_explanation",
        "wp_delta_home",
    ):
        if key in snapshot and snapshot[key] is not None:
            fields[key] = snapshot[key]
    return fields


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _apply_wp_explanation(
    snapshot_kwargs: dict[str, Any],
    *,
    live_wp_updated: bool,
    previous_snapshot: dict[str, Any],
    previous_state: GameLiveState | None,
    new_state: GameLiveState,
    new_events: list[NormalizedGameEvent],
    home_label: str,
    away_label: str,
) -> None:
    """Attach major-swing text when WP moved enough; otherwise carry prior text."""
    carry = _wp_fields_from_snapshot(previous_snapshot)
    if not live_wp_updated:
        if "wp_explanation" in carry:
            snapshot_kwargs["wp_explanation"] = carry["wp_explanation"]
        if "wp_delta_home" in carry:
            snapshot_kwargs["wp_delta_home"] = carry["wp_delta_home"]
        return

    new_home = _optional_float(snapshot_kwargs.get("home_win_proba"))
    prev_home = _optional_float(previous_snapshot.get("home_win_proba"))
    if new_home is None:
        return

    explanation = build_wp_swing_explanation(
        previous_home_wp=prev_home,
        new_home_wp=new_home,
        previous_state=previous_state,
        new_state=new_state,
        new_events=new_events,
        home_label=home_label,
        away_label=away_label,
    )
    if explanation is not None:
        snapshot_kwargs["wp_explanation"] = explanation["text"]
        snapshot_kwargs["wp_delta_home"] = explanation["delta_home_wp"]
    else:
        # Keep the last major-swing note until another major swing replaces it.
        if "wp_explanation" in carry:
            snapshot_kwargs["wp_explanation"] = carry["wp_explanation"]
        if "wp_delta_home" in carry:
            snapshot_kwargs["wp_delta_home"] = carry["wp_delta_home"]



def sync_live_game(
    db: Session,
    game_pk: int,
    *,
    rate_limiter: PollRateLimiter | None = None,
    retries: int | None = None,
    backoff_seconds: float | None = None,
) -> dict[str, Any]:
    """
    Fetch live feed for ``game_pk``, update ``Game`` state, append new events.

    On meaningful events (run, out, pitching change, end of inning), runs the
    active in-game model, upserts a live ``Prediction``, and includes WP in the
    Redis / WebSocket snapshot. Major home-WP swings (≥5pp) also get a
    rule-based ``wp_explanation`` string.

    Returns a summary dict with keys ``game_pk``, ``events_inserted``,
    ``status``, ``detailed_state``, and optionally ``live_wp_updated``.
    Raises ``MLBAPIError`` on fetch failure after retries.
    """
    settings = get_settings()
    max_retries = settings.live_sync_retries if retries is None else retries
    backoff_s = (
        settings.live_sync_backoff_seconds
        if backoff_seconds is None
        else backoff_seconds
    )

    game = db.scalar(
        select(Game)
        .options(joinedload(Game.home_team), joinedload(Game.away_team))
        .where(Game.game_pk == game_pk)
    )
    if game is None:
        raise ValueError(f"Game {game_pk} not found in database")

    previous_state = _previous_live_state(game)
    previous_snapshot = dict(game.live_state) if isinstance(game.live_state, dict) else {}
    previous_pitcher_id = previous_snapshot.get("pitcher_id")
    if previous_pitcher_id is not None:
        try:
            previous_pitcher_id = int(previous_pitcher_id)
        except (TypeError, ValueError):
            previous_pitcher_id = None

    last_exc: Exception | None = None
    live_feed: dict[str, Any] | None = None
    for attempt in range(max_retries + 1):
        try:
            if rate_limiter is not None:
                rate_limiter.wait()
            live_feed = fetch_live_feed(game_pk)
            break
        except MLBAPIError as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            delay = backoff_s * (2**attempt)
            logger.warning(
                "MLB live feed failed for game_pk=%s (attempt %s/%s); retrying in %.1fs",
                game_pk,
                attempt + 1,
                max_retries + 1,
                delay,
            )
            time.sleep(delay)

    if live_feed is None:
        assert last_exc is not None
        raise last_exc

    state = normalize_game_state(
        live_feed,
        fallback_status=game.status,
        fallback_detailed_state=game.detailed_state,
    )
    _apply_live_state(game, state)

    existing_by_id = _existing_events_by_id(db, game_pk)
    events = normalize_play_events(live_feed)
    inserted, new_events = _upsert_events(db, game_pk, events, existing_by_id)

    current_pitcher_id = extract_pitcher_id(live_feed)
    existing_live = get_live_prediction_for_game_pk(db, game_pk)
    live_wp_updated = False
    live_prediction = existing_live

    if should_run_live_inference(
        previous_state=previous_state,
        new_state=state,
        new_events=new_events,
        previous_pitcher_id=previous_pitcher_id,
        current_pitcher_id=current_pitcher_id,
        has_existing_prediction=existing_live is not None,
    ):
        try:
            inferred = generate_live_prediction_for_game(db, game, live_feed)
        except Exception:
            logger.exception("Live WP inference failed for game_pk=%s", game_pk)
            inferred = None
        if inferred is not None:
            live_prediction = inferred
            live_wp_updated = True

    wp = win_probability_payload(live_prediction)
    # Keep prior WP on the snapshot when this tick did not re-infer.
    carry = _wp_fields_from_snapshot(previous_snapshot)
    snapshot_kwargs: dict[str, Any] = {
        "events_inserted": inserted,
        "pitcher_id": current_pitcher_id
        if current_pitcher_id is not None
        else carry.get("pitcher_id"),
    }
    if wp is not None:
        snapshot_kwargs.update(wp)
    else:
        for key in ("home_win_proba", "away_win_proba", "model_version_id", "model_run_id"):
            if key in carry:
                snapshot_kwargs[key] = carry[key]

    home_label = game.home_team.abbreviation if game.home_team else "Home"
    away_label = game.away_team.abbreviation if game.away_team else "Away"
    _apply_wp_explanation(
        snapshot_kwargs,
        live_wp_updated=live_wp_updated,
        previous_snapshot=previous_snapshot,
        previous_state=previous_state,
        new_state=state,
        new_events=new_events,
        home_label=home_label,
        away_label=away_label,
    )

    snapshot = serialize_live_state(game_pk, state, **snapshot_kwargs)
    game.live_state = snapshot

    db.commit()
    cache_live_state(game_pk, state, **snapshot_kwargs)

    prev_home_wp = _optional_float(previous_snapshot.get("home_win_proba"))
    new_home_wp = _optional_float(snapshot_kwargs.get("home_win_proba"))
    try:
        from baseball_backend.services.alert_rules import evaluate_live_game_alerts

        evaluate_live_game_alerts(
            db,
            game,
            previous_state=previous_state,
            new_state=state,
            previous_home_wp=prev_home_wp,
            new_home_wp=new_home_wp,
            live_wp_updated=live_wp_updated,
            pitcher_id=current_pitcher_id,
        )
    except Exception:
        logger.exception("Alert rules failed for game_pk=%s", game_pk)

    summary: dict[str, Any] = {
        "game_pk": game_pk,
        "events_inserted": inserted,
        "status": state.status,
        "detailed_state": state.detailed_state,
        "live_wp_updated": live_wp_updated,
    }
    if wp is not None:
        summary["home_win_proba"] = wp["home_win_proba"]
        summary["away_win_proba"] = wp["away_win_proba"]
    if snapshot_kwargs.get("wp_explanation"):
        summary["wp_explanation"] = snapshot_kwargs["wp_explanation"]
        summary["wp_delta_home"] = snapshot_kwargs.get("wp_delta_home")
    return summary


def _is_live_candidate(game: Game | ScheduledGame) -> bool:
    if isinstance(game, Game):
        status = game.status
        detailed = game.detailed_state
    else:
        status = game.status
        detailed = game.detailed_state
    return is_live_tracking_status(status, detailed)


def list_live_game_pks_for_date(db: Session, game_date: str) -> list[int]:
    """
    Return game PKs that should be polled for live updates on ``game_date``.

    Combines in-progress rows already in Postgres with the MLB schedule so
    newly started games are picked up even before a full schedule re-sync.
    """
    db_games = db.scalars(
        select(Game).where(Game.game_date == date.fromisoformat(game_date))
    ).all()
    live_pks = {game.game_pk for game in db_games if _is_live_candidate(game)}

    try:
        scheduled = fetch_schedule_for_date(game_date, hydrate_probable=False)
    except MLBAPIError:
        scheduled = []

    for game in scheduled:
        if _is_live_candidate(game):
            live_pks.add(game.game_pk)

    return sorted(live_pks)


def sync_live_games_for_date(
    db: Session,
    game_date: str,
    *,
    game_delay_seconds: float | None = None,
    rate_limiter: PollRateLimiter | None = None,
) -> list[dict[str, Any]]:
    """Poll all live candidates for ``game_date`` with rate limiting between games."""
    settings = get_settings()
    if rate_limiter is not None:
        limiter = rate_limiter
    elif game_delay_seconds is None:
        limiter = PollRateLimiter(
            max(
                settings.live_poll_game_delay_seconds,
                settings.live_poll_min_request_interval_seconds,
            )
        )
    else:
        # Explicit delay (including 0 in tests) wins over the configured minimum.
        limiter = PollRateLimiter(game_delay_seconds)

    summaries: list[dict[str, Any]] = []
    game_pks = list_live_game_pks_for_date(db, game_date)
    failures = 0

    for game_pk in game_pks:
        try:
            summaries.append(sync_live_game(db, game_pk, rate_limiter=limiter))
        except (MLBAPIError, ValueError) as exc:
            failures += 1
            summaries.append(
                {
                    "game_pk": game_pk,
                    "events_inserted": 0,
                    "error": str(exc),
                }
            )

    if not game_pks:
        # Idle slate is healthy — clear any prior ok=false so the UI does not
        # keep showing "Live data degraded" after games end or before sync.
        set_live_feed_health(ok=True)
    elif failures == len(game_pks):
        set_live_feed_health(
            ok=False,
            error=f"All {failures} live poll(s) failed for {game_date}",
        )
    elif failures == 0:
        set_live_feed_health(ok=True)
    else:
        set_live_feed_health(
            ok=True,
            error=f"{failures}/{len(game_pks)} live poll(s) failed for {game_date}",
        )

    return summaries
