"""Season team/pitcher tables via Baseball Savant (with MLB team/GS joins).

Public helpers keep the historical FanGraphs-oriented column names (``wRC+``,
``FIP``, ``Team``, ``GS``, ``IP``) so feature builders and the API stay stable.
Offense quality is PA-weighted Savant xwOBA scaled to a ~100 index; pitching
uses FIP computed from Savant counting stats.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from baseball_analyze.data.cache_utils import default_cache_dir, load_or_compute
from baseball_analyze.data.mlb_client import (
    fetch_season_stat_splits,
    team_id_to_abbrev_map,
)
from baseball_analyze.data.savant_client import (
    fetch_custom_pitcher_season,
    fetch_expected_statistics,
)
from baseball_analyze.data.team_mapping import mlb_abbrev_to_fangraphs

# Approximate FIP constant; relative team diffs cancel it, absolutes stay near ERA scale.
_FIP_CONSTANT = 3.20


def _cache_dir(explicit: Optional[Path]) -> Path:
    return explicit if explicit is not None else default_cache_dir()


def _ip_to_outs(ip_value: object) -> float:
    """Convert baseball IP notation (e.g. 108.2) to outs."""
    if ip_value is None or (isinstance(ip_value, float) and pd.isna(ip_value)):
        return 0.0
    text = str(ip_value).strip()
    if not text or text.lower() in {"nan", "none"}:
        return 0.0
    if "." in text:
        whole_s, frac_s = text.split(".", 1)
        whole = int(whole_s or "0")
        frac = int(frac_s or "0")
        return float(whole * 3 + frac)
    return float(text) * 3.0


def _ip_to_float(ip_value: object) -> float:
    return _ip_to_outs(ip_value) / 3.0


def _compute_fip(hr: float, bb: float, hbp: float, so: float, ip: float) -> float:
    if ip <= 0:
        return float("nan")
    return ((13.0 * hr) + (3.0 * (bb + hbp)) - (2.0 * so)) / ip + _FIP_CONSTANT


def _mlb_player_team_gs(
    season: int,
    *,
    group: str,
) -> pd.DataFrame:
    """
    One preferred row per player: MLB abbrev, FanGraphs-style team code, GS, IP.

    Multi-team players keep the split with the most innings (pitching) or PA
    (hitting).
    """
    splits = fetch_season_stat_splits(season, group=group)
    id_to_abbrev = team_id_to_abbrev_map()
    rows: list[dict[str, object]] = []
    for split in splits:
        player = split.get("player") or {}
        team = split.get("team") or {}
        stat = split.get("stat") or {}
        pid = player.get("id")
        tid = team.get("id")
        if pid is None or tid is None:
            continue
        mlb_abbr = id_to_abbrev.get(int(tid)) or team.get("abbreviation")
        if not mlb_abbr:
            continue
        fg_team = mlb_abbrev_to_fangraphs(str(mlb_abbr), season)
        if group == "pitching":
            weight = _ip_to_float(stat.get("inningsPitched"))
            gs = float(stat.get("gamesStarted") or 0)
        else:
            weight = float(stat.get("plateAppearances") or 0)
            gs = 0.0
        rows.append(
            {
                "player_id": int(pid),
                "Team": fg_team,
                "mlb_abbrev": str(mlb_abbr).upper(),
                "GS": gs,
                "IP": weight if group == "pitching" else float("nan"),
                "PA": weight if group == "hitting" else float("nan"),
                "_weight": weight,
            }
        )
    if not rows:
        return pd.DataFrame(columns=["player_id", "Team", "mlb_abbrev", "GS", "IP", "PA"])
    df = pd.DataFrame(rows)
    # Prefer the club where the player accumulated the most PA/IP for Team label,
    # but keep season-total GS so starter/reliever cuts match Savant season rows.
    team_pick = (
        df.sort_values(["player_id", "_weight"], ascending=[True, False])
        .drop_duplicates(subset=["player_id"], keep="first")
        [["player_id", "Team", "mlb_abbrev", "IP", "PA"]]
    )
    gs_totals = df.groupby("player_id", as_index=False)["GS"].sum()
    out = team_pick.merge(gs_totals, on="player_id", how="left")
    return out.reset_index(drop=True)


def _team_offense_table(season: int) -> pd.DataFrame:
    bat = fetch_expected_statistics(season, player_type="batter", min_pa=1)
    if bat.empty:
        return pd.DataFrame(columns=["Team", "wRC+", "wOBA", "xwOBA", "PA"])

    bat = bat.rename(
        columns={
            "est_woba": "xwOBA",
            "woba": "wOBA",
            "pa": "PA",
        }
    )
    bat["player_id"] = pd.to_numeric(bat["player_id"], errors="coerce")
    bat = bat.dropna(subset=["player_id"]).copy()
    bat["player_id"] = bat["player_id"].astype(int)
    bat["PA"] = pd.to_numeric(bat["PA"], errors="coerce").fillna(0.0)
    bat["wOBA"] = pd.to_numeric(bat["wOBA"], errors="coerce")
    bat["xwOBA"] = pd.to_numeric(bat["xwOBA"], errors="coerce")
    bat["offense"] = bat["xwOBA"].fillna(bat["wOBA"])

    teams = _mlb_player_team_gs(season, group="hitting")
    merged = bat.merge(teams[["player_id", "Team"]], on="player_id", how="inner")
    merged = merged[merged["PA"] > 0].copy()
    if merged.empty:
        return pd.DataFrame(columns=["Team", "wRC+", "wOBA", "xwOBA", "PA"])

    merged["_w_off"] = merged["offense"] * merged["PA"]
    merged["_w_woba"] = merged["wOBA"].fillna(0.0) * merged["PA"]
    merged["_w_xwoba"] = merged["xwOBA"].fillna(merged["wOBA"]).fillna(0.0) * merged["PA"]
    g = merged.groupby("Team", observed=True).agg(
        PA=("PA", "sum"),
        _w_off=("_w_off", "sum"),
        _w_woba=("_w_woba", "sum"),
        _w_xwoba=("_w_xwoba", "sum"),
    )
    g["wOBA"] = g["_w_woba"] / g["PA"]
    g["xwOBA"] = g["_w_xwoba"] / g["PA"]
    offense = g["_w_off"] / g["PA"]
    league = float(offense.mean()) if len(offense) else 0.320
    if league <= 0:
        league = 0.320
    # Keep historical column name expected by feature builders / analytics.
    g["wRC+"] = (offense / league) * 100.0
    out = g[["wRC+", "wOBA", "xwOBA", "PA"]].reset_index()
    out = out.set_index("Team", drop=False)
    return out


def _pitcher_season_table(season: int) -> pd.DataFrame:
    pit = fetch_custom_pitcher_season(season, min_ip=1)
    if pit.empty:
        return pd.DataFrame(
            columns=["player_id", "Name", "Team", "FIP", "GS", "IP", "xERA", "G"]
        )

    rename = {
        "last_name, first_name": "Name",
        "p_game": "G",
        "p_formatted_ip": "IP_raw",
        "p_home_run": "HR",
        "p_strikeout": "SO",
        "p_walk": "BB",
        "p_hit_by_pitch": "HBP",
        "xera": "xERA",
    }
    pit = pit.rename(columns=rename)
    pit["player_id"] = pd.to_numeric(pit["player_id"], errors="coerce")
    pit = pit.dropna(subset=["player_id"]).copy()
    pit["player_id"] = pit["player_id"].astype(int)
    for col in ("HR", "SO", "BB", "HBP", "G", "xERA"):
        if col in pit.columns:
            pit[col] = pd.to_numeric(pit[col], errors="coerce").fillna(0.0)
    pit["IP"] = pit["IP_raw"].map(_ip_to_float)
    pit["FIP"] = [
        _compute_fip(hr, bb, hbp, so, ip)
        for hr, bb, hbp, so, ip in zip(
            pit["HR"], pit["BB"], pit["HBP"], pit["SO"], pit["IP"]
        )
    ]

    teams = _mlb_player_team_gs(season, group="pitching")
    merged = pit.merge(teams[["player_id", "Team", "GS"]], on="player_id", how="left")
    # If MLB join misses a pitcher, leave Team/GS missing; callers filter/fillna.
    merged["GS"] = pd.to_numeric(merged["GS"], errors="coerce").fillna(0.0)
    cols = ["player_id", "Name", "Team", "FIP", "GS", "IP", "xERA", "G"]
    return merged[cols].copy()


def _team_pitching_table(season: int) -> pd.DataFrame:
    pitchers = _pitcher_season_table(season)
    pitchers = pitchers.dropna(subset=["Team"]).copy()
    pitchers = pitchers[pitchers["IP"] > 0].copy()
    if pitchers.empty:
        return pd.DataFrame(columns=["Team", "FIP", "IP"])
    pitchers["_w"] = pitchers["FIP"] * pitchers["IP"]
    g = pitchers.groupby("Team", observed=True).agg(
        _w=("_w", "sum"),
        IP=("IP", "sum"),
    )
    g["FIP"] = g["_w"] / g["IP"]
    out = g[["FIP", "IP"]].reset_index().set_index("Team", drop=False)
    return out


def load_team_batting(season: int, cache_dir: Optional[Path] = None) -> pd.DataFrame:
    """One row per team with at least ``wRC+`` (Savant xwOBA scaled to ~100)."""

    def compute() -> pd.DataFrame:
        return _team_offense_table(season)

    return load_or_compute(
        "savant_team_batting",
        {"season": season, "offense": "pa_weighted_xwoba_index_v1"},
        compute,
        cache_dir=_cache_dir(cache_dir),
    )


def load_team_pitching(season: int, cache_dir: Optional[Path] = None) -> pd.DataFrame:
    """One row per team with IP-weighted FIP from Savant counting stats."""

    def compute() -> pd.DataFrame:
        return _team_pitching_table(season)

    return load_or_compute(
        "savant_team_pitching",
        {"season": season, "fip": "savant_components_v1"},
        compute,
        cache_dir=_cache_dir(cache_dir),
    )


def load_pitcher_season_stats(season: int, cache_dir: Optional[Path] = None) -> pd.DataFrame:
    """Pitcher rows with Team, FIP, GS, IP (Savant + MLB team/GS join)."""

    def compute() -> pd.DataFrame:
        df = _pitcher_season_table(season)
        bad = df["Team"].isna() | df["Team"].astype(str).str.contains("-", regex=False)
        return df.loc[~bad].copy()

    return load_or_compute(
        "savant_pitching_stats",
        {"season": season, "min_ip": 1, "join": "mlb_team_gs_v1"},
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
        rel = rel.assign(_wip=rel["FIP"].astype(float) * rel["IP"].astype(float))
        g = rel.groupby("Team", observed=True).agg(
            _wip_sum=("_wip", "sum"),
            ip_sum=("IP", "sum"),
        )
        return g["_wip_sum"] / g["ip_sum"].astype(float)

    return load_or_compute(
        "savant_bullpen_fip",
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
        "savant_median_starter_fip",
        {"season": season},
        compute,
        cache_dir=_cache_dir(cache_dir),
    )
