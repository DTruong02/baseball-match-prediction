"""Thin client for statsapi.mlb.com (schedule, boxscore, teams)."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Dict, Iterator, Optional, Tuple

import httpx

BASE = "https://statsapi.mlb.com/api/v1"


class MLBAPIError(RuntimeError):
    pass


_team_id_to_abbrev: Optional[Dict[int, str]] = None
_player_id_to_pitch_hand: Dict[int, str] = {}
_team_ops_split_cache: Dict[tuple[int, int, str], Optional[float]] = {}
_pitcher_kbb9_cache: Dict[tuple[int, int], Optional[float]] = {}


def _team_id_abbrev_lookup() -> dict[int, str]:
    global _team_id_to_abbrev
    if _team_id_to_abbrev is None:
        _team_id_to_abbrev = team_id_to_abbrev_map()
    return _team_id_to_abbrev


def _get(
    path: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    timeout_s: float = 20.0,
    retries: int = 2,
    backoff_s: float = 0.5,
) -> Dict[str, Any]:
    """
    GET JSON from MLB Stats API with a hard timeout and simple retries.

    This is intentionally conservative to avoid indefinite hangs during training.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout_s) as client:
                r = client.get(f"{BASE}{path}", params=params)
                if r.status_code != 200:
                    raise MLBAPIError(
                        f"MLB API {path} failed: {r.status_code} {r.text[:200]}"
                    )
                return r.json()
        except (httpx.HTTPError, MLBAPIError) as e:
            last_exc = e
            if attempt >= retries:
                break
            time.sleep(backoff_s * (2**attempt))
    raise MLBAPIError(f"MLB API {path} failed after retries: {last_exc}")


@dataclass
class ScheduledGame:
    game_pk: int
    game_date: str
    season: int
    status: str
    detailed_state: str
    home_team_id: int
    away_team_id: int
    home_abbrev: str
    away_abbrev: str
    venue_id: Optional[int]
    home_probable_id: Optional[int]
    away_probable_id: Optional[int]
    venue_name: Optional[str] = None
    home_probable_name: Optional[str] = None
    away_probable_name: Optional[str] = None
    home_score: Optional[int] = None
    away_score: Optional[int] = None


def fetch_teams(sport_id: int = 1) -> list[dict[str, Any]]:
    return _get("/teams", {"sportId": sport_id})["teams"]


def team_id_to_abbrev_map(sport_id: int = 1) -> dict[int, str]:
    return {t["id"]: t["abbreviation"] for t in fetch_teams(sport_id)}


def _team_abbrev(team: dict[str, Any]) -> str:
    ab = team.get("abbreviation") or team.get("teamCode") or team.get("fileCode")
    if ab:
        return str(ab)
    tid = team.get("id")
    if tid is not None:
        mp = _team_id_abbrev_lookup()
        if int(tid) in mp:
            return mp[int(tid)]
    raise KeyError("team missing abbreviation and unknown id")


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_game(raw: dict[str, Any]) -> ScheduledGame:
    teams = raw["teams"]
    home = teams["home"]["team"]
    away = teams["away"]["team"]
    hp = teams["home"].get("probablePitcher") or {}
    ap = teams["away"].get("probablePitcher") or {}
    venue = raw.get("venue") or {}
    return ScheduledGame(
        game_pk=int(raw["gamePk"]),
        game_date=str(raw["gameDate"][:10]),
        season=int(raw["season"]),
        status=str(raw["status"]["abstractGameState"]),
        detailed_state=str(raw["status"]["detailedState"]),
        home_team_id=int(home["id"]),
        away_team_id=int(away["id"]),
        home_abbrev=_team_abbrev(home),
        away_abbrev=_team_abbrev(away),
        venue_id=int(venue["id"]) if venue.get("id") else None,
        home_probable_id=int(hp["id"]) if hp.get("id") else None,
        away_probable_id=int(ap["id"]) if ap.get("id") else None,
        venue_name=str(venue["name"]) if venue.get("name") else None,
        home_probable_name=str(hp["fullName"]) if hp.get("fullName") else None,
        away_probable_name=str(ap["fullName"]) if ap.get("fullName") else None,
        home_score=_optional_int(teams["home"].get("score")),
        away_score=_optional_int(teams["away"].get("score")),
    )


def fetch_schedule_by_game_pk(
    game_pk: int,
    sport_id: int = 1,
    hydrate_probable: bool = True,
) -> Optional[ScheduledGame]:
    """Single game from schedule (works without knowing the calendar date)."""
    params: dict[str, Any] = {"sportId": sport_id, "gamePk": game_pk}
    if hydrate_probable:
        params["hydrate"] = "probablePitcher(note),venue"
    data = _get("/schedule", params)
    for d in data.get("dates") or []:
        for g in d.get("games") or []:
            return _parse_game(g)
    return None


def fetch_schedule_for_date(
    game_date: str,
    sport_id: int = 1,
    hydrate_probable: bool = True,
) -> list[ScheduledGame]:
    """game_date: YYYY-MM-DD."""
    params: dict[str, Any] = {"sportId": sport_id, "date": game_date}
    if hydrate_probable:
        params["hydrate"] = "probablePitcher(note),venue"
    data = _get("/schedule", params)
    out: list[ScheduledGame] = []
    for d in data.get("dates") or []:
        for g in d.get("games") or []:
            out.append(_parse_game(g))
    return out


def fetch_schedule_season(
    season: int,
    sport_id: int = 1,
    game_type: str = "R",
) -> Iterator[ScheduledGame]:
    """Yield all regular-season games for a year (paginated by date in API response)."""
    data = _get(
        "/schedule",
        {"sportId": sport_id, "season": season, "gameType": game_type},
    )
    for d in data.get("dates") or []:
        for g in d.get("games") or []:
            try:
                yield _parse_game(g)
            except (KeyError, TypeError, ValueError):
                continue


def fetch_boxscore(game_pk: int) -> dict[str, Any]:
    return _get(f"/game/{game_pk}/boxscore")


def fetch_linescore(game_pk: int) -> dict[str, Any]:
    return _get(f"/game/{game_pk}/linescore")


def fetch_pitcher_season_fip_xfip(mlb_player_id: int, season: int) -> Tuple[Optional[float], Optional[float]]:
    """Season pitching FIP/xFIP from MLB sabermetrics stats (no FanGraphs id required)."""
    try:
        j = _get(
            f"/people/{mlb_player_id}/stats",
            {"stats": "sabermetrics", "group": "pitching", "season": season},
        )
        splits = j["stats"][0]["splits"]
        if not splits:
            return None, None
        st = splits[0]["stat"]
        fip = st.get("fip")
        xfip = st.get("xfip")
        if fip is None:
            return None, None
        xf = float(xfip) if xfip is not None else None
        return float(fip), xf
    except (KeyError, IndexError, TypeError, ValueError, MLBAPIError):
        return None, None


def fetch_player_pitch_hand(mlb_player_id: int) -> Optional[str]:
    """
    Return pitch hand code: 'R' or 'L' when known.
    Cached in-process to avoid repeated calls during training.
    """
    pid = int(mlb_player_id)
    if pid in _player_id_to_pitch_hand:
        return _player_id_to_pitch_hand[pid]
    try:
        j = _get(f"/people/{pid}")
        people = j.get("people") or []
        if not people:
            return None
        ph = (people[0].get("pitchHand") or {}).get("code")
        if ph in ("R", "L"):
            _player_id_to_pitch_hand[pid] = ph
            return ph
        return None
    except (KeyError, IndexError, TypeError, ValueError, MLBAPIError):
        return None


def fetch_pitcher_season_kbb9(mlb_player_id: int, season: int) -> Optional[float]:
    """
    Return (K/9 - BB/9) from MLB season pitching stats when available.
    This complements FIP for starter quality.
    """
    key = (int(mlb_player_id), int(season))
    if key in _pitcher_kbb9_cache:
        return _pitcher_kbb9_cache[key]
    try:
        j = _get(
            f"/people/{mlb_player_id}/stats",
            {"stats": "season", "group": "pitching", "season": season},
        )
        splits = j["stats"][0]["splits"]
        if not splits:
            _pitcher_kbb9_cache[key] = None
            return None
        st = splits[0]["stat"]
        k9 = st.get("strikeoutsPer9Inn")
        bb9 = st.get("walksPer9Inn")
        if k9 is None or bb9 is None:
            _pitcher_kbb9_cache[key] = None
            return None
        val = float(k9) - float(bb9)
        _pitcher_kbb9_cache[key] = val
        return val
    except (KeyError, IndexError, TypeError, ValueError, MLBAPIError):
        _pitcher_kbb9_cache[key] = None
        return None


def fetch_team_ops_vs_pitcher_hand(team_id: int, season: int, pitcher_hand: str) -> Optional[float]:
    """
    Team OPS split by pitcher handedness from MLB team stats.
    pitcher_hand: 'R' or 'L'. (Uses sitCodes: 'vr'/'vl')
    """
    ph = pitcher_hand.upper()
    if ph not in ("R", "L"):
        return None
    key = (int(team_id), int(season), ph)
    if key in _team_ops_split_cache:
        return _team_ops_split_cache[key]
    sit = "vr" if ph == "R" else "vl"
    try:
        j = _get(
            f"/teams/{team_id}/stats",
            {"season": season, "stats": "season", "group": "hitting", "sportId": 1, "sitCodes": sit},
        )
        splits = j["stats"][0]["splits"]
        if not splits:
            _team_ops_split_cache[key] = None
            return None
        ops_raw = splits[0]["stat"].get("ops")
        if ops_raw is None:
            _team_ops_split_cache[key] = None
            return None
        # MLB often returns ops like ".762" as a string
        if isinstance(ops_raw, str):
            val = float("0" + ops_raw) if ops_raw.startswith(".") else float(ops_raw)
        else:
            val = float(ops_raw)
        _team_ops_split_cache[key] = val
        return val
    except (KeyError, IndexError, TypeError, ValueError, MLBAPIError):
        _team_ops_split_cache[key] = None
        return None


def extract_starting_pitcher_ids(box: dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    """Return (home_pitcher_mlb_id, away_pitcher_mlb_id) from a boxscore payload."""

    def starter_for(side: str) -> Optional[int]:
        players = box["teams"][side]["players"]
        for _pid, payload in players.items():
            pit = payload.get("stats", {}).get("pitching") or {}
            if pit.get("gamesStarted", 0) and int(pit.get("gamesStarted", 0)) >= 1:
                return int(payload["person"]["id"])
        return None

    return starter_for("home"), starter_for("away")


def home_team_won_from_linescore(linescore: dict[str, Any]) -> Optional[bool]:
    """None if unscored or tied."""
    teams = linescore.get("teams") or {}
    try:
        home_runs = int(teams["home"]["runs"])
        away_runs = int(teams["away"]["runs"])
    except (KeyError, TypeError, ValueError):
        return None
    if home_runs == away_runs:
        return None
    return home_runs > away_runs
