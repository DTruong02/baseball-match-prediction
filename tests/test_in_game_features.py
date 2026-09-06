"""Tests for in-game feature engineering."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from baseball_analyze.features.in_game import (
    IN_GAME_FEATURE_COLUMNS,
    InGameState,
    build_in_game_features,
    build_in_game_features_from_feed,
    build_in_game_training_rows_from_feed,
    in_game_feature_vector,
    in_game_features_to_matrix,
    iter_pre_play_states,
    state_from_linescore,
)

FIXTURE = Path(__file__).parent / "fixtures" / "live_feed_sample.json"


def _load_feed() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _fake_bullpen(_season: int, cache_dir=None) -> pd.Series:
    return pd.Series({"CWS": 3.90, "DET": 4.10})


def _fake_pitcher_fip(mlb_id: int, _season: int):
    return (3.50 if mlb_id % 2 == 0 else 4.00), 3.60


def _fake_pitcher_kbb9(mlb_id: int, _season: int):
    return 5.0 if mlb_id % 2 == 0 else 3.0


def test_iter_pre_play_states_reconstructs_first_ab() -> None:
    feed = _load_feed()
    states = list(iter_pre_play_states(feed))
    assert len(states) == 8

    first = states[0]
    assert first.at_bat_index == 0
    assert first.inning == 1
    assert first.is_top_inning is True
    assert first.outs == 0
    assert first.home_score == 0
    assert first.away_score == 0
    assert first.runner_on_1b is False
    assert first.runner_on_2b is False
    assert first.runner_on_3b is False
    assert first.pitcher_id == 675512
    assert first.batter_id == 803011
    assert first.is_reliever is False


def test_iter_pre_play_states_carries_bases_after_double() -> None:
    feed = _load_feed()
    states = list(iter_pre_play_states(feed))
    second = states[1]
    assert second.at_bat_index == 1
    assert second.runner_on_2b is True
    assert second.runner_on_1b is False
    assert second.outs == 0


def test_state_from_linescore_final_fixture() -> None:
    feed = _load_feed()
    state = state_from_linescore(feed)
    assert state is not None
    assert state.home_score == 3
    assert state.away_score == 4
    assert state.inning == 9
    assert state.is_top_inning is False
    assert state.outs == 3
    assert state.pitcher_id == 663542
    assert state.batter_id == 679529


@patch("baseball_analyze.features.in_game.fetch_pitcher_season_kbb9", side_effect=_fake_pitcher_kbb9)
@patch("baseball_analyze.features.in_game.fetch_pitcher_season_fip_xfip", side_effect=_fake_pitcher_fip)
@patch("baseball_analyze.features.in_game.bullpen_fip_by_team", side_effect=_fake_bullpen)
@patch(
    "baseball_analyze.features.in_game.mlb_abbrev_to_fangraphs",
    side_effect=lambda abbr, _season: {"CWS": "CWS", "CHW": "CWS", "DET": "DET"}.get(abbr, abbr),
)
def test_build_in_game_features_score_and_columns(_map, _bull, _fip, _kbb9) -> None:
    state = InGameState(
        home_score=2,
        away_score=5,
        inning=6,
        is_top_inning=True,
        outs=1,
        balls=2,
        strikes=1,
        runner_on_1b=True,
        runner_on_3b=True,
        pitcher_id=100,
        batter_id=200,
        pitch_hand="R",
        bat_side="R",
        is_reliever=True,
        at_bat_index=42,
    )
    row = build_in_game_features(
        state,
        game_pk=1,
        season=2024,
        home_abbrev="CWS",
        away_abbrev="DET",
    )
    assert set(row.features) == set(IN_GAME_FEATURE_COLUMNS)
    assert row.features["score_diff"] == -3.0
    assert row.features["inning"] == 6.0
    assert row.features["is_top_inning"] == 1.0
    assert row.features["outs"] == 1.0
    assert row.features["runner_on_1b"] == 1.0
    assert row.features["runner_on_2b"] == 0.0
    assert row.features["runner_on_3b"] == 1.0
    assert row.features["balls"] == 2.0
    assert row.features["strikes"] == 1.0
    assert row.features["same_hand_matchup"] == 1.0
    assert row.features["is_reliever"] == 1.0
    # Top inning => home pitching => CWS bullpen
    assert row.features["defense_bullpen_fip"] == 3.90
    assert row.features["pitcher_fip"] == 3.50
    assert row.features["pitcher_kbb9"] == 5.0

    vec = in_game_feature_vector(row)
    assert vec.shape == (len(IN_GAME_FEATURE_COLUMNS),)
    assert vec.dtype == float


@patch("baseball_analyze.features.in_game.fetch_pitcher_season_kbb9", side_effect=_fake_pitcher_kbb9)
@patch("baseball_analyze.features.in_game.fetch_pitcher_season_fip_xfip", side_effect=_fake_pitcher_fip)
@patch("baseball_analyze.features.in_game.bullpen_fip_by_team", side_effect=_fake_bullpen)
@patch(
    "baseball_analyze.features.in_game.mlb_abbrev_to_fangraphs",
    side_effect=lambda abbr, _season: {"CWS": "CWS", "CHW": "CWS", "DET": "DET"}.get(abbr, abbr),
)
def test_training_rows_from_feed(_map, _bull, _fip, _kbb9) -> None:
    feed = _load_feed()
    rows = build_in_game_training_rows_from_feed(
        feed,
        game_pk=824239,
        season=2026,
        home_abbrev="CWS",
        away_abbrev="DET",
        home_won=False,
    )
    assert len(rows) == 8
    assert all(r.label_home_win is False for r in rows)
    assert all(set(r.features) == set(IN_GAME_FEATURE_COLUMNS) for r in rows)

    matrix = in_game_features_to_matrix(rows)
    assert matrix.shape == (8, len(IN_GAME_FEATURE_COLUMNS))
    assert np.isfinite(matrix).all()

    # First row: score tied, empty bases
    assert rows[0].features["score_diff"] == 0.0
    assert rows[0].features["runner_on_2b"] == 0.0
    # Second row: runner on 2B after the leadoff double
    assert rows[1].features["runner_on_2b"] == 1.0


@patch("baseball_analyze.features.in_game.fetch_pitcher_season_kbb9", side_effect=_fake_pitcher_kbb9)
@patch("baseball_analyze.features.in_game.fetch_pitcher_season_fip_xfip", side_effect=_fake_pitcher_fip)
@patch("baseball_analyze.features.in_game.bullpen_fip_by_team", side_effect=_fake_bullpen)
@patch(
    "baseball_analyze.features.in_game.mlb_abbrev_to_fangraphs",
    side_effect=lambda abbr, _season: {"CWS": "CWS", "CHW": "CWS", "DET": "DET"}.get(abbr, abbr),
)
def test_build_from_feed_live_helper(_map, _bull, _fip, _kbb9) -> None:
    feed = _load_feed()
    row = build_in_game_features_from_feed(
        feed,
        game_pk=824239,
        season=2026,
        home_abbrev="CWS",
        away_abbrev="DET",
    )
    assert row is not None
    assert row.features["score_diff"] == -1.0
    assert row.features["inning"] == 9.0
    assert row.features["is_top_inning"] == 0.0
