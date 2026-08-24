"""Build numeric feature rows for pregame home-win modeling."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from baseball_analyze.data.fangraphs_features import (
    bullpen_fip_by_team,
    load_team_batting,
    load_team_pitching,
    median_starter_fip_by_team,
)
from baseball_analyze.data.mlb_client import (
    ScheduledGame,
    fetch_pitcher_season_fip_xfip,
    fetch_pitcher_season_kbb9,
    fetch_player_pitch_hand,
    fetch_team_ops_vs_pitcher_hand,
)
from baseball_analyze.data.park_data import park_factor_for_home_team
from baseball_analyze.data.team_mapping import mlb_abbrev_to_fangraphs

FEATURE_COLUMNS: list[str] = [
    "diff_wrc_plus",
    "diff_ops_vs_sp_hand",
    "diff_team_fip",
    "diff_starter_fip",
    "diff_starter_kbb9",
    "diff_bullpen_fip",
    "park_factor_runs",
    "home_field",
]


@dataclass
class FeatureRow:
    game_pk: int
    season: int
    home_fg: str
    away_fg: str
    features: dict[str, float]
    notes: list[str]


def _get_team_stat(
    table: pd.DataFrame,
    team: str,
    col: str,
    default: float,
) -> float:
    if team not in table.index:
        return default
    v = table.loc[team, col]
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def build_features_for_game(
    game: ScheduledGame,
    cache_dir: Path | None = None,
    missing_pitcher_policy: Literal["median", "nan"] = "median",
) -> FeatureRow:
    """
    Construct a feature dict for one scheduled game.

    Higher values favor the home team for diff_* features (except park_factor is raw).
    - diff_wrc_plus: home - away wRC+
    - diff_team_fip: away_fip - home_fip (better home pitching => positive)
    - diff_starter_fip: away - home starter FIP
    - diff_bullpen_fip: away - home bullpen FIP
    """
    season = game.season
    h = mlb_abbrev_to_fangraphs(game.home_abbrev, season)
    a = mlb_abbrev_to_fangraphs(game.away_abbrev, season)

    bat = load_team_batting(season, cache_dir=cache_dir)
    pit = load_team_pitching(season, cache_dir=cache_dir)
    bull = bullpen_fip_by_team(season, cache_dir=cache_dir)
    med_st = median_starter_fip_by_team(season, cache_dir=cache_dir)

    notes: list[str] = []

    hw = _get_team_stat(bat, h, "wRC+", 100.0)
    aw = _get_team_stat(bat, a, "wRC+", 100.0)
    hfip = _get_team_stat(pit, h, "FIP", 4.0)
    afip = _get_team_stat(pit, a, "FIP", 4.0)

    def team_ops_vs_hand(team_id: int, pitcher_id: int | None) -> float:
        """
        Platoon/matchup context: team OPS vs the opposing starter's pitch hand.
        Falls back to a neutral-ish league OPS if we can't resolve the pitcher hand or split.
        """
        neutral_ops = 0.720
        if pitcher_id is None:
            return neutral_ops
        ph = fetch_player_pitch_hand(pitcher_id)
        if ph is None:
            return neutral_ops
        ops = fetch_team_ops_vs_pitcher_hand(team_id, season, ph)
        if ops is None:
            return neutral_ops
        return float(ops)

    def starter_fip(mlb_pid: int | None, fg_team: str) -> float:
        if mlb_pid is None:
            notes.append(f"Missing probable pitcher for {fg_team}; using fallback.")
            if missing_pitcher_policy == "median":
                v = med_st.get(fg_team)
                if v is not None and not pd.isna(v):
                    return float(v)
            return hfip if fg_team == h else afip  # last resort: team FIP
        fip, _ = fetch_pitcher_season_fip_xfip(mlb_pid, season)
        if fip is None:
            notes.append(f"No MLB sabermetrics FIP for pitcher mlbam={mlb_pid}; fallback.")
            v = med_st.get(fg_team)
            if missing_pitcher_policy == "median" and v is not None and not pd.isna(v):
                return float(v)
            return _get_team_stat(pit, fg_team, "FIP", 4.0)
        return fip

    def starter_kbb9(mlb_pid: int | None) -> float:
        """
        Starter quality beyond FIP: (K/9 - BB/9) from MLB season pitching stats.
        If missing, return 0.0 (model can learn to ignore when noisy/missing).
        """
        if mlb_pid is None:
            return 0.0
        val = fetch_pitcher_season_kbb9(mlb_pid, season)
        if val is None:
            notes.append(f"No MLB K/9 and BB/9 for pitcher mlbam={mlb_pid}; kbb9 fallback.")
            return 0.0
        return float(val)
    # Starter FIP
    h_s = starter_fip(game.home_probable_id, h)
    a_s = starter_fip(game.away_probable_id, a)
    # Starter K-BB (per 9)
    h_kbb9 = starter_kbb9(game.home_probable_id)
    a_kbb9 = starter_kbb9(game.away_probable_id)
    # Bullpen FIP
    hb = float(bull.get(h, hfip))
    ab = float(bull.get(a, afip))
    # Park Factor
    pf = park_factor_for_home_team(h)
    # Platoon/matchup offense: home offense faces away starter; away offense faces home starter
    home_ops = team_ops_vs_hand(game.home_team_id, game.away_probable_id)
    away_ops = team_ops_vs_hand(game.away_team_id, game.home_probable_id)
    # Features
    feats = {
        "diff_wrc_plus": hw - aw,
        "diff_ops_vs_sp_hand": home_ops - away_ops,
        "diff_team_fip": afip - hfip,
        "diff_starter_fip": a_s - h_s,
        "diff_starter_kbb9": h_kbb9 - a_kbb9,
        "diff_bullpen_fip": ab - hb,
        "park_factor_runs": pf,
        "home_field": 1.0,
    }

    return FeatureRow(
        game_pk=game.game_pk,
        season=season,
        home_fg=h,
        away_fg=a,
        features=feats,
        notes=notes,
    )


def feature_vector(row: FeatureRow) -> np.ndarray:
    return np.array([row.features[c] for c in FEATURE_COLUMNS], dtype=float)


def features_dict_to_matrix(rows: list[FeatureRow]) -> np.ndarray:
    return np.vstack([feature_vector(r) for r in rows])
