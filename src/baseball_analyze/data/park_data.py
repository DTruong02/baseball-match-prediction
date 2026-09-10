"""Approximate home park run factors (FanGraphs-style, ~100 = neutral).

Defaults are illustrative multi-year averages. Optional season overlays nudge a
few parks that changed character (new venues / dimensions). Refresh from
FanGraphs park factors when you need exact current-year precision.
"""

from __future__ import annotations

from typing import Optional

# FanGraphs-style three-letter codes (aligned with team mapping / Savant joins).
PARK_FACTOR_RUNS: dict[str, float] = {
    "ARI": 99.0,   # Chase (humidor era) closer to neutral than old desert parks
    "ATL": 100.0,
    "BAL": 103.0,  # Camden Yards post-wall move still slightly hit-friendly
    "BOS": 105.0,
    "CHC": 101.0,
    "CHW": 100.0,
    "CIN": 108.0,
    "CLE": 98.0,
    "COL": 112.0,
    "DET": 99.0,
    "HOU": 100.0,
    "KCR": 101.0,
    "LAA": 99.0,
    "LAD": 99.0,
    "MIA": 97.0,
    "MIL": 101.0,
    "MIN": 100.0,
    "NYM": 96.0,   # Citi Field pitcher-leaning
    "NYY": 102.0,
    "OAK": 97.0,
    "ATH": 97.0,   # Athletics temporary / Sacramento-era proxy
    "PHI": 101.0,
    "PIT": 99.0,
    "SDP": 97.0,
    "SEA": 94.0,
    "SFG": 96.0,
    "STL": 99.0,
    "TBR": 98.0,
    "TEX": 103.0,  # Globe Life Field
    "TOR": 101.0,
    "WSN": 100.0,
}

# Sparse overlays for seasons where a venue change materially shifts runs.
_SEASON_PARK_OVERLAYS: dict[int, dict[str, float]] = {
    # Globe Life Field opened 2020; keep slightly above neutral.
    2020: {"TEX": 102.0},
    2021: {"TEX": 103.0},
    2022: {"TEX": 103.0},
    2023: {"TEX": 103.0, "ARI": 99.0},
    2024: {"TEX": 103.0, "ARI": 99.0, "ATH": 97.0, "OAK": 97.0},
    2025: {"TEX": 103.0, "ARI": 99.0, "ATH": 97.0, "OAK": 97.0},
    2026: {"TEX": 103.0, "ARI": 99.0, "ATH": 97.0, "OAK": 97.0},
}


def park_factor_for_home_team(fg_team: str, *, season: Optional[int] = None) -> float:
    base = float(PARK_FACTOR_RUNS.get(fg_team, 100.0))
    if season is None:
        return base
    overlay = _SEASON_PARK_OVERLAYS.get(int(season)) or {}
    return float(overlay.get(fg_team, base))
