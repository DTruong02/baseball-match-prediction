"""Tests for rule-based live WP swing explanations."""

from __future__ import annotations

import pytest

from baseball_backend.services.live_normalize import GameLiveState, NormalizedGameEvent
from baseball_backend.services.live_wp_explanations import (
    MAJOR_WP_SWING_THRESHOLD,
    build_wp_swing_explanation,
    collect_swing_reasons,
)


def _state(
    *,
    home: int = 0,
    away: int = 0,
    inning: int = 5,
    is_top: bool = True,
    outs: int = 0,
) -> GameLiveState:
    return GameLiveState(
        home_score=home,
        away_score=away,
        status="Live",
        detailed_state="In Progress",
        current_inning=inning,
        inning_state="Top" if is_top else "Bottom",
        is_top_inning=is_top,
        outs=outs,
        balls=0,
        strikes=0,
    )


def test_build_explanation_for_home_scoring_swing() -> None:
    prev = _state(home=1, away=2, outs=1)
    new = _state(home=4, away=2, outs=1)
    out = build_wp_swing_explanation(
        previous_home_wp=0.40,
        new_home_wp=0.58,
        previous_state=prev,
        new_state=new,
        home_label="NYY",
        away_label="BOS",
    )
    assert out is not None
    assert out["delta_home_wp"] == pytest.approx(0.18)
    assert out["reasons"] == ["NYY scored 3 runs"]
    assert out["text"] == "NYY scored 3 runs; WP +18%"


def test_no_explanation_below_threshold() -> None:
    prev = _state(home=1, away=1)
    new = _state(home=1, away=1, outs=1)
    out = build_wp_swing_explanation(
        previous_home_wp=0.50,
        new_home_wp=0.50 + MAJOR_WP_SWING_THRESHOLD - 0.001,
        previous_state=prev,
        new_state=new,
    )
    assert out is None


def test_no_explanation_without_previous_wp() -> None:
    out = build_wp_swing_explanation(
        previous_home_wp=None,
        new_home_wp=0.70,
        previous_state=None,
        new_state=_state(),
    )
    assert out is None


def test_away_scoring_negative_delta() -> None:
    prev = _state(home=3, away=1)
    new = _state(home=3, away=3)
    out = build_wp_swing_explanation(
        previous_home_wp=0.72,
        new_home_wp=0.55,
        previous_state=prev,
        new_state=new,
        home_label="NYY",
        away_label="BOS",
    )
    assert out is not None
    assert out["text"] == "BOS scored 2 runs; WP -17%"


def test_pitching_change_reason() -> None:
    state = _state()
    events = [
        NormalizedGameEvent(
            event_id="play-1",
            type="play",
            sequence=1,
            payload={
                "event_type": "pitching_substitution",
                "event": "Pitching Substitution",
            },
        )
    ]
    reasons = collect_swing_reasons(
        previous_state=state,
        new_state=state,
        new_events=events,
    )
    assert reasons == ["Pitching change"]


def test_strikeout_reason_preferred_over_generic_out() -> None:
    prev = _state(outs=0)
    new = _state(outs=1)
    events = [
        NormalizedGameEvent(
            event_id="play-2",
            type="play",
            sequence=2,
            payload={
                "event_type": "strikeout",
                "event": "Strikeout",
                "is_complete": True,
                "is_scoring_play": False,
            },
        )
    ]
    reasons = collect_swing_reasons(
        previous_state=prev,
        new_state=new,
        new_events=events,
    )
    assert reasons == ["Strikeout"]


def test_inning_ended_reason() -> None:
    prev = _state(inning=5, is_top=True, outs=2)
    new = _state(inning=5, is_top=False, outs=0)
    reasons = collect_swing_reasons(previous_state=prev, new_state=new, new_events=[])
    assert reasons == ["Inning ended"]
