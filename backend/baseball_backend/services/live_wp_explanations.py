"""Rule-based text for major live win-probability swings."""

from __future__ import annotations

from typing import Any

from baseball_backend.services.live_normalize import GameLiveState, NormalizedGameEvent

# Absolute change in home win probability (0–1) that counts as a "major" swing.
MAJOR_WP_SWING_THRESHOLD = 0.05

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


def _format_wp_delta(delta_home: float) -> str:
    """Format home-WP delta as percentage points, e.g. ``WP +18%`` / ``WP -5%``."""
    points = int(round(delta_home * 100))
    if points == 0:
        points = 1 if delta_home > 0 else -1 if delta_home < 0 else 0
    sign = "+" if points > 0 else ""
    return f"WP {sign}{points}%"


def _run_phrase(n: int) -> str:
    return "run" if n == 1 else "runs"


def _score_reasons(
    previous_state: GameLiveState | None,
    new_state: GameLiveState,
    *,
    home_label: str,
    away_label: str,
) -> list[str]:
    if previous_state is None:
        return []
    reasons: list[str] = []
    home_delta = new_state.home_score - previous_state.home_score
    away_delta = new_state.away_score - previous_state.away_score
    if home_delta > 0:
        reasons.append(f"{home_label} scored {home_delta} {_run_phrase(home_delta)}")
    if away_delta > 0:
        reasons.append(f"{away_label} scored {away_delta} {_run_phrase(away_delta)}")
    return reasons


def _event_type_label(payload: dict[str, Any]) -> str | None:
    event = str(payload.get("event") or "").strip()
    if event:
        return event
    event_type = str(payload.get("event_type") or "").strip().replace("_", " ")
    return event_type.title() if event_type else None


def _pitching_change_reason(new_events: list[NormalizedGameEvent]) -> str | None:
    for event in new_events:
        payload = event.payload or {}
        combined = (
            f"{payload.get('event_type') or ''} {payload.get('event') or ''}".lower()
        )
        if any(token in combined for token in _PITCHING_CHANGE_TOKENS):
            return "Pitching change"
    return None


def _out_reason(
    previous_state: GameLiveState | None,
    new_state: GameLiveState,
    new_events: list[NormalizedGameEvent],
) -> str | None:
    for event in new_events:
        payload = event.payload or {}
        if payload.get("is_scoring_play"):
            continue
        event_type = str(payload.get("event_type") or "").lower()
        event_name = str(payload.get("event") or "").lower()
        if any(token in event_type for token in _OUT_EVENT_TOKENS) or (
            "out" in event_name and payload.get("is_complete")
        ):
            label = _event_type_label(payload)
            return label if label else "Out recorded"

    if previous_state is None:
        return None
    prev_outs = previous_state.outs
    new_outs = new_state.outs
    if prev_outs is None or new_outs is None:
        return None
    if new_outs > prev_outs:
        return "Out recorded"
    if (
        new_outs == 0
        and prev_outs >= 2
        and (
            previous_state.current_inning != new_state.current_inning
            or previous_state.is_top_inning != new_state.is_top_inning
        )
    ):
        return "Inning ended"
    return None


def _inning_reason(
    previous_state: GameLiveState | None,
    new_state: GameLiveState,
) -> str | None:
    if previous_state is None:
        return None
    if (
        previous_state.current_inning != new_state.current_inning
        or previous_state.is_top_inning != new_state.is_top_inning
    ):
        half = new_state.inning_state
        if not half and new_state.is_top_inning is not None:
            half = "Top" if new_state.is_top_inning else "Bottom"
        if half and new_state.current_inning is not None:
            return f"New half-inning ({half} {new_state.current_inning})"
        return "New half-inning"
    return None


def collect_swing_reasons(
    *,
    previous_state: GameLiveState | None,
    new_state: GameLiveState,
    new_events: list[NormalizedGameEvent] | None = None,
    home_label: str = "Home",
    away_label: str = "Away",
) -> list[str]:
    """
    Build short, rule-based reason phrases for a state/event delta.

    Prefers scoring plays, then pitching changes, outs, then half-inning changes.
    """
    events = new_events or []
    reasons = _score_reasons(
        previous_state,
        new_state,
        home_label=home_label,
        away_label=away_label,
    )
    if reasons:
        return reasons

    pitching = _pitching_change_reason(events)
    if pitching:
        return [pitching]

    out = _out_reason(previous_state, new_state, events)
    if out:
        return [out]

    inning = _inning_reason(previous_state, new_state)
    if inning:
        return [inning]

    return ["Game state updated"]


def build_wp_swing_explanation(
    *,
    previous_home_wp: float | None,
    new_home_wp: float,
    previous_state: GameLiveState | None,
    new_state: GameLiveState,
    new_events: list[NormalizedGameEvent] | None = None,
    home_label: str = "Home",
    away_label: str = "Away",
    threshold: float = MAJOR_WP_SWING_THRESHOLD,
) -> dict[str, Any] | None:
    """
    Explain a major home-WP swing, or return ``None`` if below ``threshold``.

    Example::

        {
            "text": "Home scored 3 runs; WP +18%",
            "delta_home_wp": 0.18,
            "reasons": ["Home scored 3 runs"],
        }
    """
    if previous_home_wp is None:
        return None

    delta = float(new_home_wp) - float(previous_home_wp)
    if abs(delta) < threshold:
        return None

    reasons = collect_swing_reasons(
        previous_state=previous_state,
        new_state=new_state,
        new_events=new_events,
        home_label=home_label,
        away_label=away_label,
    )
    reason_text = "; ".join(reasons) if len(reasons) > 1 else reasons[0]
    text = f"{reason_text}; {_format_wp_delta(delta)}"
    return {
        "text": text,
        "delta_home_wp": delta,
        "reasons": reasons,
    }
