"""Map MLB API team abbreviations to FanGraphs `Team` codes."""

from __future__ import annotations


# MLB statsapi abbreviations differ from FanGraphs for several clubs.
_MLB_TO_FG: dict[str, str] = {
    "AZ": "ARI",
    "CWS": "CHW",
    "KC": "KCR",
    "SD": "SDP",
    "SF": "SFG",
    "TB": "TBR",
    "WSH": "WSN",
}


def mlb_abbrev_to_fangraphs(mlb_abbrev: str, season: int) -> str:
    """
    Convert MLB schedule/boxscore abbreviation to FanGraphs team column.

    The Athletics franchise moved; FanGraphs used OAK before 2025 and ATH from 2025 on.
    """
    a = mlb_abbrev.upper()
    if a == "ATH":
        return "OAK" if season <= 2024 else "ATH"
    return _MLB_TO_FG.get(a, a)
