"""Build numeric feature rows for pregame home-win modeling.

In-game (live WP) features live in ``baseball_analyze.features.in_game`` and are
intentionally separate from the pregame columns below.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

import numpy as np
import pandas as pd

from baseball_analyze.data.cache_utils import pregame_stats_as_of
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
    pitcher_recent_start_fip,
    pitcher_rest_days,
    prefetch_as_of_pitcher_stats,
)
from baseball_analyze.data.park_data import park_factor_for_home_team
from baseball_analyze.data.team_mapping import mlb_abbrev_to_fangraphs

FEATURE_COLUMNS: list[str] = [
    "diff_wrc_plus",
    "diff_ops_vs_sp_hand",
    "diff_team_fip",
    "diff_starter_fip",
    "diff_starter_xfip",
    "diff_starter_kbb9",
    "diff_starter_rest_days",
    "diff_starter_recent_fip",
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
    game_date: str = ""


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
    *,
    as_of: Optional[str] = None,
    use_as_of: bool = True,
) -> FeatureRow:
    """
    Construct a feature dict for one scheduled game.

    Higher values favor the home team for diff_* features (except park_factor is raw).
    - diff_wrc_plus: home - away wRC+ (or OPS index when as-of)
    - diff_team_fip: away_fip - home_fip (better home pitching => positive)
    - diff_starter_fip / xfip: away - home starter rates
    - diff_starter_rest_days: home_rest - away_rest
    - diff_starter_recent_fip: away_l3 - home_l3 recent start FIP
    - diff_bullpen_fip: away - home bullpen FIP

    By default stats are frozen through the day before ``game.game_date`` (no
    same-day / future leakage). Pass ``use_as_of=False`` to use full-season/YTD
    Savant tables (legacy behavior).
    """
    season = game.season
    game_day = str(game.game_date or "")[:10]
    if use_as_of:
        stats_as_of = (str(as_of)[:10] if as_of else None) or (
            pregame_stats_as_of(game_day) if game_day else None
        )
    else:
        stats_as_of = None

    h = mlb_abbrev_to_fangraphs(game.home_abbrev, season)
    a = mlb_abbrev_to_fangraphs(game.away_abbrev, season)

    if stats_as_of:
        prefetch_as_of_pitcher_stats(season, stats_as_of)

    bat = load_team_batting(season, cache_dir=cache_dir, as_of=stats_as_of)
    pit = load_team_pitching(season, cache_dir=cache_dir, as_of=stats_as_of)
    bull = bullpen_fip_by_team(season, cache_dir=cache_dir, as_of=stats_as_of)
    med_st = median_starter_fip_by_team(season, cache_dir=cache_dir, as_of=stats_as_of)

    notes: list[str] = []
    if stats_as_of:
        notes.append(f"as_of={stats_as_of}")

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
        ops = fetch_team_ops_vs_pitcher_hand(
            team_id, season, ph, as_of=stats_as_of
        )
        if ops is None:
            return neutral_ops
        return float(ops)

    def starter_rates(mlb_pid: int | None, fg_team: str) -> tuple[float, float, float]:
        """Return (fip, xfip_or_fip, kbb9) for a probable starter."""
        if mlb_pid is None:
            notes.append(f"Missing probable pitcher for {fg_team}; using fallback.")
            if missing_pitcher_policy == "median":
                v = med_st.get(fg_team)
                if v is not None and not pd.isna(v):
                    fv = float(v)
                    return fv, fv, 0.0
            team_fip = hfip if fg_team == h else afip
            return team_fip, team_fip, 0.0

        fip, xfip = fetch_pitcher_season_fip_xfip(mlb_pid, season, as_of=stats_as_of)
        if fip is None:
            notes.append(f"No MLB FIP for pitcher mlbam={mlb_pid}; fallback.")
            v = med_st.get(fg_team)
            if missing_pitcher_policy == "median" and v is not None and not pd.isna(v):
                fv = float(v)
                fip = fv
            else:
                fip = _get_team_stat(pit, fg_team, "FIP", 4.0)
            xfip = fip
        else:
            if xfip is None:
                xfip = fip
        kbb9 = fetch_pitcher_season_kbb9(mlb_pid, season, as_of=stats_as_of)
        if kbb9 is None:
            notes.append(f"No MLB K/9 and BB/9 for pitcher mlbam={mlb_pid}; kbb9 fallback.")
            kbb9 = 0.0
        return float(fip), float(xfip), float(kbb9)

    def starter_rest(mlb_pid: int | None) -> float:
        if mlb_pid is None or not game_day:
            return 5.0  # neutral-ish starter rest
        rest = pitcher_rest_days(mlb_pid, season, game_day)
        if rest is None:
            return 5.0
        # Cap extreme layoffs so long IL stints don't dominate.
        return float(min(max(rest, 0.0), 21.0))

    def starter_recent(mlb_pid: int | None, season_fip: float) -> float:
        if mlb_pid is None or not game_day:
            return season_fip
        recent = pitcher_recent_start_fip(mlb_pid, season, game_day, last_n=3)
        if recent is None:
            return season_fip
        return float(recent)

    h_s, h_x, h_kbb9 = starter_rates(game.home_probable_id, h)
    a_s, a_x, a_kbb9 = starter_rates(game.away_probable_id, a)
    h_rest = starter_rest(game.home_probable_id)
    a_rest = starter_rest(game.away_probable_id)
    h_recent = starter_recent(game.home_probable_id, h_s)
    a_recent = starter_recent(game.away_probable_id, a_s)

    hb = float(bull.get(h, hfip))
    ab = float(bull.get(a, afip))
    pf = park_factor_for_home_team(h, season=season)

    home_ops = team_ops_vs_hand(game.home_team_id, game.away_probable_id)
    away_ops = team_ops_vs_hand(game.away_team_id, game.home_probable_id)

    feats = {
        "diff_wrc_plus": hw - aw,
        "diff_ops_vs_sp_hand": home_ops - away_ops,
        "diff_team_fip": afip - hfip,
        "diff_starter_fip": a_s - h_s,
        "diff_starter_xfip": a_x - h_x,
        "diff_starter_kbb9": h_kbb9 - a_kbb9,
        "diff_starter_rest_days": h_rest - a_rest,
        "diff_starter_recent_fip": a_recent - h_recent,
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
        game_date=game_day,
    )


def feature_vector(row: FeatureRow) -> np.ndarray:
    return np.array([row.features[c] for c in FEATURE_COLUMNS], dtype=float)


def features_dict_to_matrix(rows: list[FeatureRow]) -> np.ndarray:
    return np.vstack([feature_vector(r) for r in rows])
