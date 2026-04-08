"""Approximate home park run factors (FanGraphs-style, ~100 = neutral).

Values are illustrative defaults for modeling; refresh periodically from FanGraphs
or another source if you need current-year precision.
"""

from __future__ import annotations

# FanGraphs-style three-letter codes (aligned with fg_team_batting_data Team column).
PARK_FACTOR_RUNS: dict[str, float] = {
    "ARI": 101.0,
    "ATL": 98.0,
    "BAL": 100.0,
    "BOS": 104.0,
    "CHC": 101.0,
    "CHW": 100.0,
    "CIN": 109.0,
    "CLE": 97.0,
    "COL": 115.0,
    "DET": 100.0,
    "HOU": 101.0,
    "KCR": 100.0,
    "LAA": 99.0,
    "LAD": 102.0,
    "MIA": 96.0,
    "MIL": 101.0,
    "MIN": 99.0,
    "NYM": 100.0,
    "NYY": 101.0,
    "OAK": 99.0,
    "ATH": 99.0,
    "PHI": 101.0,
    "PIT": 100.0,
    "SDP": 100.0,
    "SEA": 95.0,
    "SFG": 101.0,
    "STL": 100.0,
    "TBR": 99.0,
    "TEX": 104.0,
    "TOR": 101.0,
    "WSN": 100.0,
}


def park_factor_for_home_team(fg_team: str) -> float:
    return float(PARK_FACTOR_RUNS.get(fg_team, 100.0))
