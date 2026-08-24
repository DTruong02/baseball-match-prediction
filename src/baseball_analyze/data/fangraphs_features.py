"""FanGraphs-backed season tables via pybaseball with disk caching."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
from pybaseball import (
    fg_team_batting_data,
    fg_team_pitching_data,
    pitching_stats,
)

from baseball_analyze.data.cache_utils import default_cache_dir, load_or_compute


def _cache_dir(explicit: Optional[Path]) -> Path:
    return explicit if explicit is not None else default_cache_dir()


def load_team_batting(season: int, cache_dir: Optional[Path] = None) -> pd.DataFrame:
    """One row per team with at least wRC+."""

    def compute() -> pd.DataFrame:
        # FanGraphs stat ids (pybaseball enums); human names like "wRC+" are not valid in recent pybaseball.
        df = fg_team_batting_data(
            season,
            stat_columns=["1", "61", "12", "6"],
        )
        df = df.set_index("Team", drop=False)
        return df

    return load_or_compute(
        "fg_team_batting",
        {"season": season, "stat_cols": "v2_enum_ids"},
        compute,
        cache_dir=_cache_dir(cache_dir),
    )


def load_team_pitching(season: int, cache_dir: Optional[Path] = None) -> pd.DataFrame:
    def compute() -> pd.DataFrame:
        df = fg_team_pitching_data(
            season,
            stat_columns=["1", "6", "45", "62"],
        )
        df = df.set_index("Team", drop=False)
        return df

    return load_or_compute(
        "fg_team_pitching",
        {"season": season, "stat_cols": "v2_enum_ids"},
        compute,
        cache_dir=_cache_dir(cache_dir),
    )


def load_pitcher_season_stats(season: int, cache_dir: Optional[Path] = None) -> pd.DataFrame:
    """Pitcher rows with IDfg, Team, FIP, GS — qual=10 keeps downloads smaller than qual=0."""

    def compute() -> pd.DataFrame:
        df = pitching_stats(season, qual=10)
        bad = df["Team"].astype(str).str.contains("-", regex=False)
        df = df.loc[~bad & df["Team"].notna()].copy()
        return df

    return load_or_compute(
        "fg_pitching_stats",
        {"season": season, "qual": 10},
        compute,
        cache_dir=_cache_dir(cache_dir),
    )


def bullpen_fip_by_team(season: int, cache_dir: Optional[Path] = None) -> pd.Series:
    """IP-weighted reliever FIP (GS == 0, min IP)."""

    def compute() -> pd.Series:
        df = load_pitcher_season_stats(season, cache_dir=cache_dir)
        rel = df[(df["GS"].fillna(0) == 0) & (df["IP"].fillna(0) >= 10.0)].copy()
        if rel.empty:
            return pd.Series(dtype=float)
        rel = rel.assign(
            _wip=rel["FIP"].astype(float) * rel["IP"].astype(float),
        )
        g = rel.groupby("Team", observed=True).agg(
            _wip_sum=("_wip", "sum"),
            ip_sum=("IP", "sum"),
        )
        return g["_wip_sum"] / g["ip_sum"].astype(float)

    return load_or_compute(
        "fg_bullpen_fip",
        {"season": season},
        compute,
        cache_dir=_cache_dir(cache_dir),
    )


def median_starter_fip_by_team(season: int, cache_dir: Optional[Path] = None) -> pd.Series:
    """Median FIP for pitchers with GS >= 8 (rotation proxy)."""

    def compute() -> pd.Series:
        df = load_pitcher_season_stats(season, cache_dir=cache_dir)
        starters = df[df["GS"].fillna(0) >= 8].copy()
        return starters.groupby("Team")["FIP"].median()

    return load_or_compute(
        "fg_median_starter_fip",
        {"season": season},
        compute,
        cache_dir=_cache_dir(cache_dir),
    )
