"""Tests for mid-season training date filters and split helpers."""

from __future__ import annotations

import datetime as dt

import pytest

from baseball_analyze.data.cache_utils import season_table_as_of_key
from baseball_analyze.models.training_filters import (
    include_game_date,
    is_completed_game,
    resolve_split_masks,
)


def test_is_completed_game():
    assert is_completed_game("Final")
    assert is_completed_game("Completed Early")
    assert not is_completed_game("In Progress")
    assert not is_completed_game("Scheduled")


def test_include_game_date_respects_through_date():
    assert include_game_date("2026-09-01")
    assert include_game_date("2026-09-01", through_date="2026-09-09")
    assert not include_game_date("2026-09-10", through_date="2026-09-09")


def test_resolve_split_masks_val_from_date_precedence():
    seasons = [2025, 2025, 2026, 2026]
    dates = ["2025-09-01", "2025-09-02", "2026-07-01", "2026-08-15"]
    split_type, mask = resolve_split_masks(
        seasons=seasons,
        game_dates=dates,
        val_seasons=[2025],
        val_from_date="2026-08-01",
    )
    assert split_type == "time_date"
    assert list(mask) == [False, False, False, True]


def test_resolve_split_masks_val_seasons():
    seasons = [2024, 2025, 2025]
    dates = ["2024-06-01", "2025-06-01", "2025-07-01"]
    split_type, mask = resolve_split_masks(
        seasons=seasons,
        game_dates=dates,
        val_seasons=[2025],
        val_from_date=None,
    )
    assert split_type == "time"
    assert list(mask) == [False, True, True]


def test_resolve_split_masks_empty_means_random():
    split_type, mask = resolve_split_masks(
        seasons=[2024, 2025],
        game_dates=["2024-06-01", "2025-06-01"],
        val_seasons=[],
        val_from_date=None,
    )
    assert split_type == "random"
    assert mask is None


def test_resolve_split_masks_rejects_all_val():
    with pytest.raises(RuntimeError, match="captured all rows"):
        resolve_split_masks(
            seasons=[2026, 2026],
            game_dates=["2026-08-01", "2026-08-02"],
            val_seasons=[],
            val_from_date="2026-08-01",
        )


def test_season_table_as_of_key_current_vs_final():
    today = dt.date(2026, 9, 9)
    assert season_table_as_of_key(2025, today=today) == "final"
    assert season_table_as_of_key(2026, today=today) == "2026-09-09"


def test_season_table_as_of_key_explicit_snapshot():
    assert season_table_as_of_key(2024, as_of="2024-06-15") == "2024-06-15"
    assert season_table_as_of_key(2024, as_of=dt.date(2024, 6, 15)) == "2024-06-15"


def test_pregame_stats_as_of():
    from baseball_analyze.data.cache_utils import pregame_stats_as_of

    assert pregame_stats_as_of("2024-06-15") == "2024-06-14"
    assert pregame_stats_as_of(dt.date(2024, 6, 15)) == "2024-06-14"
