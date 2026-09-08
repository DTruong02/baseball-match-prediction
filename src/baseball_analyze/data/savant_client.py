"""HTTP helpers for Baseball Savant leaderboard CSV downloads."""

from __future__ import annotations

from io import StringIO
from typing import Any, Mapping, Optional
from urllib.parse import urlencode

import httpx
import pandas as pd

SAVANT_BASE = "https://baseballsavant.mlb.com"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,*/*",
}


class SavantAPIError(RuntimeError):
    pass


def fetch_csv(
    path: str,
    params: Optional[Mapping[str, Any]] = None,
    *,
    timeout_s: float = 60.0,
) -> pd.DataFrame:
    """
    GET a Savant CSV endpoint and return a DataFrame.

    ``path`` may be absolute under ``baseballsavant.mlb.com`` (e.g.
    ``/leaderboard/expected_statistics``) or a full URL.
    """
    if path.startswith("http"):
        url = path
        if params:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{urlencode(params)}"
    else:
        query = dict(params or {})
        query.setdefault("csv", "true")
        url = f"{SAVANT_BASE}{path}?{urlencode(query)}"

    try:
        with httpx.Client(timeout=timeout_s, headers=_DEFAULT_HEADERS, follow_redirects=True) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise SavantAPIError(f"Savant request failed for {url}: {exc}") from exc

    if response.status_code >= 400:
        raise SavantAPIError(
            f"Savant {url} failed: {response.status_code} {response.text[:200]}"
        )

    content_type = (response.headers.get("content-type") or "").lower()
    text = response.content.decode("utf-8-sig", errors="replace")
    if "html" in content_type or text.lstrip().lower().startswith("<!doctype"):
        raise SavantAPIError(
            f"Savant returned HTML instead of CSV for {url}; "
            "endpoint may require different query params."
        )

    try:
        return pd.read_csv(StringIO(text))
    except Exception as exc:  # noqa: BLE001 - surface parse failures clearly
        raise SavantAPIError(f"Failed to parse Savant CSV from {url}: {exc}") from exc


def fetch_expected_statistics(
    season: int,
    *,
    player_type: str,
    min_pa: int | str = 1,
) -> pd.DataFrame:
    """Batter or pitcher expected-stats leaderboard (includes wOBA / xwOBA / xERA)."""
    return fetch_csv(
        "/leaderboard/expected_statistics",
        {
            "type": player_type,
            "year": int(season),
            "position": "",
            "team": "",
            "min": min_pa,
            "csv": "true",
        },
    )


def fetch_custom_pitcher_season(season: int, *, min_ip: int | str = 1) -> pd.DataFrame:
    """Pitcher season counting stats used to compute FIP."""
    selections = ",".join(
        [
            "p_game",
            "p_formatted_ip",
            "p_home_run",
            "p_strikeout",
            "p_walk",
            "p_hit_by_pitch",
            "woba",
            "est_woba",
            "xera",
        ]
    )
    return fetch_csv(
        "/leaderboard/custom",
        {
            "year": int(season),
            "type": "pitcher",
            "filter": "",
            "sort": "4",
            "sortDir": "desc",
            "min": min_ip,
            "selections": selections,
            "chart": "false",
            "csv": "true",
        },
    )
