from unittest.mock import patch

import pandas as pd

from baseball_analyze.features import build_features_for_game
from baseball_analyze.mlb_client import ScheduledGame


def _fake_team_batting(_season: int, cache_dir=None) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Team": ["NYY", "BOS"],
            "wRC+": [110.0, 105.0],
        }
    ).set_index("Team", drop=False)


def _fake_team_pitching(_season: int, cache_dir=None) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Team": ["NYY", "BOS"],
            "FIP": [3.8, 4.0],
        }
    ).set_index("Team", drop=False)


def _fake_bullpen(_season: int, cache_dir=None) -> pd.Series:
    return pd.Series({"NYY": 3.9, "BOS": 4.1})


def _fake_median_starter(_season: int, cache_dir=None) -> pd.Series:
    return pd.Series({"NYY": 4.0, "BOS": 4.0})


def _fake_pitcher_fip(_mlb_id: int, _season: int):
    if _mlb_id == 1:
        return 3.5, 3.6
    return 3.7, 3.8


def _fake_pitcher_kbb9(_mlb_id: int, _season: int):
    # home pitcher (1) slightly better than away pitcher (2)
    return 4.5 if _mlb_id == 1 else 3.5


def _fake_pitch_hand(_mlb_id: int):
    return "R"


def _fake_team_ops_vs_hand(_team_id: int, _season: int, _hand: str):
    # home team faces RHP -> .760, away team faces RHP -> .720
    return 0.760 if _team_id == 147 else 0.720


@patch("baseball_analyze.features.fetch_team_ops_vs_pitcher_hand", side_effect=_fake_team_ops_vs_hand)
@patch("baseball_analyze.features.fetch_player_pitch_hand", side_effect=_fake_pitch_hand)
@patch("baseball_analyze.features.fetch_pitcher_season_kbb9", side_effect=_fake_pitcher_kbb9)
@patch("baseball_analyze.features.fetch_pitcher_season_fip_xfip", side_effect=_fake_pitcher_fip)
@patch(
    "baseball_analyze.features.median_starter_fip_by_team",
    side_effect=_fake_median_starter,
)
@patch("baseball_analyze.features.bullpen_fip_by_team", side_effect=_fake_bullpen)
@patch("baseball_analyze.features.load_team_pitching", side_effect=_fake_team_pitching)
@patch("baseball_analyze.features.load_team_batting", side_effect=_fake_team_batting)
def test_build_features_mocked(
    _lb,
    _lp,
    _bull,
    _med,
    _pf,
    _kbb9,
    _hand,
    _ops,
) -> None:
    g = ScheduledGame(
        game_pk=1,
        game_date="2025-04-06",
        season=2025,
        status="Preview",
        detailed_state="Scheduled",
        home_team_id=147,
        away_team_id=111,
        home_abbrev="NYY",
        away_abbrev="BOS",
        venue_id=3313,
        home_probable_id=1,
        away_probable_id=2,
    )
    fr = build_features_for_game(g)
    assert fr.features["diff_wrc_plus"] == 110.0 - 105.0
    assert fr.features["diff_ops_vs_sp_hand"] == 0.760 - 0.720
    assert fr.features["diff_team_fip"] == 4.0 - 3.8
    assert fr.features["diff_starter_fip"] == 3.7 - 3.5
    assert fr.features["diff_starter_kbb9"] == 4.5 - 3.5
    assert fr.features["home_field"] == 1.0
    assert "park_factor_runs" in fr.features
