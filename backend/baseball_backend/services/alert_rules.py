"""Alert rules for followed games — fire from workers, not API request handlers.

Rules (Stage 6.3):
- ``game_start`` — transition into Warmup / Live
- ``wp_threshold`` — live home WP moves by at least the user's ``wp_threshold_pct``
- ``high_leverage`` — late innings, runners on, close score
- ``game_final`` — game reaches a final state
- ``new_prediction`` — first pregame prediction for a model version

Each (user, alert_type, dedupe_key) is claimed once via ``AlertDispatch`` so poll
loops stay idempotent. Delivery still goes through ``enqueue_notification``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Optional

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from baseball_backend.db.models import (
    AlertDispatch,
    Game,
    NotificationAlertType,
    Prediction,
    UserFollow,
)
from baseball_backend.services.live_normalize import GameLiveState
from baseball_backend.services.notification_service import (
    enqueue_notification,
    get_or_create_preferences,
)

logger = logging.getLogger(__name__)

_FINAL_STATES = frozenset({"Final", "Game Over", "Completed Early"})

_UNDERWAY_DETAILED = frozenset(
    {
        "Warmup",
        "In Progress",
        "Delayed",
        "Delayed Start",
        "Manager Challenge",
        "Suspended",
    }
)

_HIGH_LEVERAGE_MIN_INNING = 7
_HIGH_LEVERAGE_MAX_MARGIN = 2


def _is_underway(status: str | None, detailed_state: str | None) -> bool:
    if status == "Live":
        return True
    return (detailed_state or "") in _UNDERWAY_DETAILED


def _is_final(detailed_state: str | None) -> bool:
    return (detailed_state or "") in _FINAL_STATES


def _matchup_label(game: Game) -> str:
    away = game.away_team.abbreviation if game.away_team else "AWAY"
    home = game.home_team.abbreviation if game.home_team else "HOME"
    return f"{away} @ {home}"


def _bases_code(state: GameLiveState) -> str:
    return (
        f"{int(bool(state.on_1b))}"
        f"{int(bool(state.on_2b))}"
        f"{int(bool(state.on_3b))}"
    )


def _runners_on(state: GameLiveState) -> bool:
    return bool(state.on_1b or state.on_2b or state.on_3b)


def _is_high_leverage(state: GameLiveState) -> bool:
    if state.detailed_state in _FINAL_STATES or state.status == "Final":
        return False
    if not _is_underway(state.status, state.detailed_state):
        return False
    inning = state.current_inning
    if inning is None or inning < _HIGH_LEVERAGE_MIN_INNING:
        return False
    if not _runners_on(state):
        return False
    margin = abs(state.home_score - state.away_score)
    return margin <= _HIGH_LEVERAGE_MAX_MARGIN


def follower_user_ids_for_game(
    db: Session,
    game: Game,
    *,
    extra_player_ids: Optional[set[int]] = None,
) -> set[int]:
    """Users who follow either side's team or a relevant player."""
    team_ids = {game.home_team_id, game.away_team_id}
    player_ids: set[int] = set(extra_player_ids or ())
    if game.home_probable_pitcher_id is not None:
        player_ids.add(game.home_probable_pitcher_id)
    if game.away_probable_pitcher_id is not None:
        player_ids.add(game.away_probable_pitcher_id)

    clauses = [
        UserFollow.team_id.in_(team_ids),
    ]
    if player_ids:
        clauses.append(UserFollow.player_id.in_(player_ids))

    rows = db.scalars(select(UserFollow.user_id).where(or_(*clauses))).all()
    return set(rows)


def _claim_dispatch(
    db: Session,
    *,
    user_id: int,
    alert_type: str,
    dedupe_key: str,
    game_pk: int | None,
) -> bool:
    """Return True if this alert was newly claimed for the user."""
    try:
        with db.begin_nested():
            db.add(
                AlertDispatch(
                    user_id=user_id,
                    alert_type=alert_type,
                    dedupe_key=dedupe_key,
                    game_pk=game_pk,
                )
            )
            db.flush()
        return True
    except IntegrityError:
        return False


def _notify_followers(
    db: Session,
    *,
    user_ids: set[int],
    alert_type: str,
    dedupe_key: str,
    game_pk: int,
    title: str,
    body: str,
    payload: dict[str, Any],
    user_filter: Optional[Callable[[int], bool]] = None,
) -> int:
    """Claim + enqueue for each follower. Returns notifications created (rows)."""
    created_rows = 0
    for user_id in sorted(user_ids):
        if user_filter is not None and not user_filter(user_id):
            continue
        if not _claim_dispatch(
            db,
            user_id=user_id,
            alert_type=alert_type,
            dedupe_key=dedupe_key,
            game_pk=game_pk,
        ):
            continue
        rows = enqueue_notification(
            db,
            user_id=user_id,
            alert_type=alert_type,
            title=title,
            body=body,
            payload=payload,
        )
        # Persist claim even when prefs skip creating notification rows.
        if not rows:
            db.commit()
        created_rows += len(rows)
    return created_rows


def evaluate_live_game_alerts(
    db: Session,
    game: Game,
    *,
    previous_state: GameLiveState | None,
    new_state: GameLiveState,
    previous_home_wp: float | None,
    new_home_wp: float | None,
    live_wp_updated: bool,
    pitcher_id: int | None = None,
) -> dict[str, int]:
    """
    Evaluate start / WP / leverage / final rules after a live sync commit.

    Safe to call from the live worker; never raises to the caller.
    """
    counts = {
        "game_start": 0,
        "wp_threshold": 0,
        "high_leverage": 0,
        "game_final": 0,
    }
    try:
        if game.home_team is None or game.away_team is None:
            game = db.scalar(
                select(Game)
                .options(joinedload(Game.home_team), joinedload(Game.away_team))
                .where(Game.id == game.id)
            ) or game

        extra_players: set[int] = set()
        if pitcher_id is not None:
            extra_players.add(pitcher_id)
        user_ids = follower_user_ids_for_game(db, game, extra_player_ids=extra_players)
        if not user_ids:
            return counts

        label = _matchup_label(game)
        prev_status = previous_state.status if previous_state else None
        prev_detailed = previous_state.detailed_state if previous_state else None

        # --- game about to start / underway ---
        if not _is_underway(prev_status, prev_detailed) and _is_underway(
            new_state.status, new_state.detailed_state
        ):
            counts["game_start"] = _notify_followers(
                db,
                user_ids=user_ids,
                alert_type=NotificationAlertType.GAME_START.value,
                dedupe_key=f"game_start:{game.game_pk}",
                game_pk=game.game_pk,
                title=f"{label} starting",
                body=f"{label} is {new_state.detailed_state}.",
                payload={
                    "game_pk": game.game_pk,
                    "status": new_state.status,
                    "detailed_state": new_state.detailed_state,
                },
            )

        # --- game final ---
        if not _is_final(prev_detailed) and _is_final(new_state.detailed_state):
            winner = game.winner or "TIE"
            counts["game_final"] = _notify_followers(
                db,
                user_ids=user_ids,
                alert_type=NotificationAlertType.GAME_FINAL.value,
                dedupe_key=f"game_final:{game.game_pk}",
                game_pk=game.game_pk,
                title=f"Final: {label}",
                body=(
                    f"{label} final {new_state.away_score}-{new_state.home_score}"
                    f" (winner {winner})."
                ),
                payload={
                    "game_pk": game.game_pk,
                    "home_score": new_state.home_score,
                    "away_score": new_state.away_score,
                    "winner": winner,
                },
            )

        # --- WP threshold (per-user magnitude) ---
        if (
            live_wp_updated
            and previous_home_wp is not None
            and new_home_wp is not None
        ):
            delta = abs(new_home_wp - previous_home_wp)
            if delta > 0:

                def _wp_ok(user_id: int) -> bool:
                    prefs = get_or_create_preferences(db, user_id)
                    return delta >= float(prefs.wp_threshold_pct)

                direction = "up" if new_home_wp > previous_home_wp else "down"
                home = game.home_team.abbreviation if game.home_team else "HOME"
                counts["wp_threshold"] = _notify_followers(
                    db,
                    user_ids=user_ids,
                    alert_type=NotificationAlertType.WP_THRESHOLD.value,
                    dedupe_key=(
                        f"wp_threshold:{game.game_pk}:"
                        f"{previous_home_wp:.3f}:{new_home_wp:.3f}"
                    ),
                    game_pk=game.game_pk,
                    title=f"WP swing: {label}",
                    body=(
                        f"{home} win probability moved {direction} by "
                        f"{delta * 100:.0f}pp "
                        f"({previous_home_wp * 100:.0f}% → {new_home_wp * 100:.0f}%)."
                    ),
                    payload={
                        "game_pk": game.game_pk,
                        "previous_home_wp": previous_home_wp,
                        "home_win_proba": new_home_wp,
                        "delta_home_wp": new_home_wp - previous_home_wp,
                    },
                    user_filter=_wp_ok,
                )

        # --- high leverage ---
        if _is_high_leverage(new_state):
            half = "top" if new_state.is_top_inning else "bot"
            inning = new_state.current_inning or 0
            bases = _bases_code(new_state)
            runners = []
            if new_state.on_1b:
                runners.append("1B")
            if new_state.on_2b:
                runners.append("2B")
            if new_state.on_3b:
                runners.append("3B")
            runner_text = ", ".join(runners) if runners else "bases empty"
            counts["high_leverage"] = _notify_followers(
                db,
                user_ids=user_ids,
                alert_type=NotificationAlertType.HIGH_LEVERAGE.value,
                dedupe_key=f"high_leverage:{game.game_pk}:{inning}:{half}:{bases}",
                game_pk=game.game_pk,
                title=f"High leverage: {label}",
                body=(
                    f"{label} — inning {inning} ({half}), "
                    f"score {new_state.away_score}-{new_state.home_score}, "
                    f"runners on {runner_text}."
                ),
                payload={
                    "game_pk": game.game_pk,
                    "inning": inning,
                    "is_top_inning": new_state.is_top_inning,
                    "home_score": new_state.home_score,
                    "away_score": new_state.away_score,
                    "on_1b": bool(new_state.on_1b),
                    "on_2b": bool(new_state.on_2b),
                    "on_3b": bool(new_state.on_3b),
                },
            )
    except Exception:
        logger.exception("Alert evaluation failed for game_pk=%s", game.game_pk)
    return counts


def evaluate_schedule_status_alerts(
    db: Session,
    game: Game,
    *,
    previous_status: str | None,
    previous_detailed_state: str | None,
) -> dict[str, int]:
    """Fire start/final alerts after schedule sync when status transitions."""
    counts = {"game_start": 0, "game_final": 0}
    try:
        if game.home_team is None or game.away_team is None:
            game = db.scalar(
                select(Game)
                .options(joinedload(Game.home_team), joinedload(Game.away_team))
                .where(Game.id == game.id)
            ) or game

        user_ids = follower_user_ids_for_game(db, game)
        if not user_ids:
            return counts

        label = _matchup_label(game)
        new_status = game.status
        new_detailed = game.detailed_state

        if not _is_underway(previous_status, previous_detailed_state) and _is_underway(
            new_status, new_detailed
        ):
            counts["game_start"] = _notify_followers(
                db,
                user_ids=user_ids,
                alert_type=NotificationAlertType.GAME_START.value,
                dedupe_key=f"game_start:{game.game_pk}",
                game_pk=game.game_pk,
                title=f"{label} starting",
                body=f"{label} is {new_detailed}.",
                payload={
                    "game_pk": game.game_pk,
                    "status": new_status,
                    "detailed_state": new_detailed,
                },
            )

        if not _is_final(previous_detailed_state) and _is_final(new_detailed):
            home_score = game.home_score if game.home_score is not None else 0
            away_score = game.away_score if game.away_score is not None else 0
            winner = game.winner or "TIE"
            counts["game_final"] = _notify_followers(
                db,
                user_ids=user_ids,
                alert_type=NotificationAlertType.GAME_FINAL.value,
                dedupe_key=f"game_final:{game.game_pk}",
                game_pk=game.game_pk,
                title=f"Final: {label}",
                body=f"{label} final {away_score}-{home_score} (winner {winner}).",
                payload={
                    "game_pk": game.game_pk,
                    "home_score": home_score,
                    "away_score": away_score,
                    "winner": winner,
                },
            )
    except Exception:
        logger.exception(
            "Schedule alert evaluation failed for game_pk=%s", getattr(game, "game_pk", None)
        )
    return counts


def evaluate_new_prediction_alert(
    db: Session,
    game: Game,
    prediction: Prediction,
) -> int:
    """Notify followers when a new pregame prediction is persisted."""
    try:
        if game.home_team is None or game.away_team is None:
            game = db.scalar(
                select(Game)
                .options(joinedload(Game.home_team), joinedload(Game.away_team))
                .where(Game.id == game.id)
            ) or game

        user_ids = follower_user_ids_for_game(db, game)
        if not user_ids:
            return 0

        label = _matchup_label(game)
        home_pct = (
            float(prediction.home_win_proba) * 100
            if prediction.home_win_proba is not None
            else None
        )
        away_pct = (
            float(prediction.away_win_proba) * 100
            if prediction.away_win_proba is not None
            else None
        )
        if home_pct is not None and away_pct is not None:
            body = (
                f"Pregame model: {label} — "
                f"HOME {home_pct:.0f}% / AWAY {away_pct:.0f}%."
            )
        else:
            body = f"Pregame model prediction is ready for {label}."

        return _notify_followers(
            db,
            user_ids=user_ids,
            alert_type=NotificationAlertType.NEW_PREDICTION.value,
            dedupe_key=f"new_prediction:{game.game_pk}:{prediction.model_version_id}",
            game_pk=game.game_pk,
            title=f"New prediction: {label}",
            body=body,
            payload={
                "game_pk": game.game_pk,
                "model_version_id": prediction.model_version_id,
                "home_win_proba": prediction.home_win_proba,
                "away_win_proba": prediction.away_win_proba,
            },
        )
    except Exception:
        logger.exception(
            "New-prediction alert failed for game_pk=%s", getattr(game, "game_pk", None)
        )
        return 0
