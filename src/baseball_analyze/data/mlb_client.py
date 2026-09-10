"""Thin client for statsapi.mlb.com (schedule, boxscore, teams)."""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import os
import threading
import time
from typing import Any, Dict, Iterator, Optional, Tuple

import httpx

BASE = "https://statsapi.mlb.com/api/v1"

# Global spacing for all outbound MLB Stats API calls (schedule sync, live,
# training features). Override with MLB_MIN_REQUEST_INTERVAL_SECONDS.
_DEFAULT_MIN_REQUEST_INTERVAL_S = 0.08
_FIP_CONSTANT = 3.20


class MLBAPIError(RuntimeError):
    pass


class _OutboundRateLimiter:
    """Thread-safe minimum spacing between MLB HTTP requests."""

    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval = max(0.0, float(min_interval_seconds))
        self._lock = threading.Lock()
        self._last_request_at = 0.0

    def wait(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            earliest = self._last_request_at + self._min_interval
            delay = earliest - now
            if delay > 0:
                time.sleep(delay)
                now = time.monotonic()
            self._last_request_at = now


def _configured_min_request_interval() -> float:
    raw = os.environ.get("MLB_MIN_REQUEST_INTERVAL_SECONDS")
    if raw is None or raw.strip() == "":
        return _DEFAULT_MIN_REQUEST_INTERVAL_S
    try:
        return max(0.0, float(raw))
    except ValueError:
        return _DEFAULT_MIN_REQUEST_INTERVAL_S


_outbound_limiter = _OutboundRateLimiter(_configured_min_request_interval())
_http_client: Optional[httpx.Client] = None
_http_client_lock = threading.Lock()


def _shared_http_client(timeout_s: float = 20.0) -> httpx.Client:
    """Reuse one process-wide httpx client (connection pooling)."""
    global _http_client
    with _http_client_lock:
        if _http_client is None or _http_client.is_closed:
            _http_client = httpx.Client(
                base_url=BASE,
                timeout=timeout_s,
                headers={"User-Agent": "baseball-analyze/0.1"},
            )
        return _http_client


def _reset_outbound_rate_limiter_for_tests(
    min_interval_seconds: float | None = None,
) -> None:
    """Reset the process-wide limiter (tests only)."""
    global _outbound_limiter
    interval = (
        _configured_min_request_interval()
        if min_interval_seconds is None
        else max(0.0, float(min_interval_seconds))
    )
    _outbound_limiter = _OutboundRateLimiter(interval)


def _reset_http_client_for_tests() -> None:
    """Close and clear the shared HTTP client (tests only)."""
    global _http_client
    with _http_client_lock:
        if _http_client is not None and not _http_client.is_closed:
            _http_client.close()
        _http_client = None


def _clear_pitcher_stat_caches_for_tests() -> None:
    """Clear in-process pitcher/feature caches (tests only)."""
    _pitcher_fip_cache.clear()
    _pitcher_kbb9_cache.clear()
    _pitcher_game_log_cache.clear()
    _as_of_pitcher_warmed.clear()
    _team_ops_split_cache.clear()


_team_id_to_abbrev: Optional[Dict[int, str]] = None
_player_id_to_pitch_hand: Dict[int, str] = {}
_team_ops_split_cache: Dict[tuple, Optional[float]] = {}
# (player_id, season) or (player_id, season, as_of_yyyy_mm_dd)
_pitcher_kbb9_cache: Dict[tuple, Optional[float]] = {}
_pitcher_fip_cache: Dict[tuple, Tuple[Optional[float], Optional[float]]] = {}
_pitcher_game_log_cache: Dict[tuple[int, int], list[dict[str, Any]]] = {}
_as_of_pitcher_warmed: set[tuple[int, str]] = set()


def _team_id_abbrev_lookup() -> dict[int, str]:
    global _team_id_to_abbrev
    if _team_id_to_abbrev is None:
        _team_id_to_abbrev = team_id_to_abbrev_map()
    return _team_id_to_abbrev


def _retry_after_seconds(response: httpx.Response, fallback_s: float) -> float:
    """Prefer Retry-After when present; otherwise use exponential backoff fallback."""
    header = response.headers.get("Retry-After")
    if header is not None:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
    return max(0.0, fallback_s)


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

    All callers share a process-wide minimum request interval (see
    MLB_MIN_REQUEST_INTERVAL_SECONDS) and a shared httpx client. Retries
    transient transport errors and HTTP 429/5xx. Honors Retry-After on 429
    when provided. Non-retryable 4xx responses fail immediately.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            _outbound_limiter.wait()
            client = _shared_http_client(timeout_s=timeout_s)
            r = client.get(path, params=params, timeout=timeout_s)
            if r.status_code == 200:
                return r.json()

            retryable = r.status_code == 429 or r.status_code >= 500
            message = f"MLB API {path} failed: {r.status_code} {r.text[:200]}"
            if not retryable or attempt >= retries:
                raise MLBAPIError(message)

            delay = _retry_after_seconds(r, backoff_s * (2**attempt))
            time.sleep(delay)
            last_exc = MLBAPIError(message)
            continue
        except httpx.HTTPError as e:
            last_exc = e
            if attempt >= retries:
                break
            time.sleep(backoff_s * (2**attempt))
        except MLBAPIError as e:
            last_exc = e
            break
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


def fetch_season_stat_splits(
    season: int,
    *,
    group: str,
    sport_id: int = 1,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    """League-wide season hitting/pitching splits (player + team + stat)."""
    payload = _get(
        "/stats",
        {
            "stats": "season",
            "group": group,
            "season": int(season),
            "sportIds": sport_id,
            "limit": int(limit),
            "playerPool": "all",
        },
    )
    return list((payload.get("stats") or [{}])[0].get("splits") or [])


def season_stats_window_start(season: int) -> str:
    """Inclusive start date for YTD / as-of windows (covers early openers)."""
    return f"{int(season)}-03-01"


def fetch_stats_by_date_range(
    season: int,
    *,
    group: str,
    start_date: str,
    end_date: str,
    sport_id: int = 1,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    """
    League-wide player splits for ``[start_date, end_date]`` (YYYY-MM-DD).

    Used to build as-of-game-day team and pitcher tables without end-of-season leakage.
    """
    payload = _get(
        "/stats",
        {
            "stats": "byDateRange",
            "group": group,
            "season": int(season),
            "sportIds": sport_id,
            "startDate": str(start_date)[:10],
            "endDate": str(end_date)[:10],
            "limit": int(limit),
            "playerPool": "all",
        },
    )
    return list((payload.get("stats") or [{}])[0].get("splits") or [])


def prefetch_as_of_pitcher_stats(season: int, end_date: str) -> dict[str, int]:
    """
    Warm FIP/xFIP and K-BB caches for all pitchers through ``end_date`` (inclusive).

    One league-wide ``byDateRange`` pitching call; FIP is computed from counting stats.
    """
    season_i = int(season)
    end = str(end_date)[:10]
    warm_key = (season_i, end)
    if warm_key in _as_of_pitcher_warmed:
        return {"pitchers": 0, "filled_kbb9": 0, "filled_fip": 0, "cached": 1}

    start = season_stats_window_start(season_i)
    if end < start:
        _as_of_pitcher_warmed.add(warm_key)
        return {"pitchers": 0, "filled_kbb9": 0, "filled_fip": 0, "cached": 0}

    best_by_ip: dict[int, tuple[float, dict[str, Any]]] = {}
    for split in fetch_stats_by_date_range(
        season_i,
        group="pitching",
        start_date=start,
        end_date=end,
    ):
        player = split.get("player") or {}
        pid = player.get("id")
        if pid is None:
            continue
        stat = split.get("stat") or {}
        ip = _ip_to_float(stat.get("inningsPitched"))
        prev = best_by_ip.get(int(pid))
        if prev is None or ip >= prev[0]:
            best_by_ip[int(pid)] = (ip, stat)

    filled_kbb9 = 0
    filled_fip = 0
    for pid, (_ip, stat) in best_by_ip.items():
        key = (pid, season_i, end)
        if key not in _pitcher_kbb9_cache:
            _pitcher_kbb9_cache[key] = _kbb9_from_stat(stat)
            filled_kbb9 += 1
        if key not in _pitcher_fip_cache:
            fip = _fip_from_season_stat(stat)
            # xFIP not available from counting-stat date range; leave None.
            _pitcher_fip_cache[key] = (fip, None)
            filled_fip += 1

    _as_of_pitcher_warmed.add(warm_key)
    return {
        "pitchers": len(best_by_ip),
        "filled_kbb9": filled_kbb9,
        "filled_fip": filled_fip,
        "cached": 0,
    }


def fetch_pitcher_game_log(mlb_player_id: int, season: int) -> list[dict[str, Any]]:
    """
    Pitcher game log rows for a season (cached in-process).

    Each item: ``{date, gs, ip, hr, bb, hbp, so, fip}`` for starts/relief appearances.
    """
    key = (int(mlb_player_id), int(season))
    if key in _pitcher_game_log_cache:
        return _pitcher_game_log_cache[key]
    rows: list[dict[str, Any]] = []
    try:
        j = _get(
            f"/people/{int(mlb_player_id)}/stats",
            {"stats": "gameLog", "group": "pitching", "season": int(season)},
        )
        splits = list((j.get("stats") or [{}])[0].get("splits") or [])
        for split in splits:
            stat = split.get("stat") or {}
            raw_date = split.get("date") or (split.get("game") or {}).get("gameDate")
            if not raw_date:
                continue
            day = str(raw_date)[:10]
            gs = int(_safe_float(stat.get("gamesStarted")) or 0)
            ip = _ip_to_float(stat.get("inningsPitched"))
            hr = _safe_float(stat.get("homeRuns")) or 0.0
            bb = _safe_float(stat.get("baseOnBalls")) or 0.0
            hbp = _safe_float(stat.get("hitByPitch")) or 0.0
            so = _safe_float(stat.get("strikeOuts")) or 0.0
            rows.append(
                {
                    "date": day,
                    "gs": gs,
                    "ip": ip,
                    "hr": hr,
                    "bb": bb,
                    "hbp": hbp,
                    "so": so,
                    "fip": _compute_fip_from_counting(hr, bb, hbp, so, ip),
                }
            )
        rows.sort(key=lambda r: r["date"])
    except (KeyError, IndexError, TypeError, ValueError, MLBAPIError):
        rows = []
    _pitcher_game_log_cache[key] = rows
    return rows


def pitcher_rest_days(
    mlb_player_id: int,
    season: int,
    game_date: str,
) -> Optional[float]:
    """
    Days since the pitcher's previous game started before ``game_date``.

    Returns None when no prior start is found (season debut / unknown).
    """
    day = str(game_date)[:10]
    prior_starts = [
        r for r in fetch_pitcher_game_log(mlb_player_id, season)
        if r["gs"] >= 1 and r["date"] < day
    ]
    if not prior_starts:
        return None
    last = prior_starts[-1]["date"]
    try:
        return float((dt.date.fromisoformat(day) - dt.date.fromisoformat(last)).days)
    except ValueError:
        return None


def pitcher_recent_start_fip(
    mlb_player_id: int,
    season: int,
    game_date: str,
    *,
    last_n: int = 3,
) -> Optional[float]:
    """IP-weighted FIP over the previous ``last_n`` starts before ``game_date``."""
    day = str(game_date)[:10]
    prior_starts = [
        r for r in fetch_pitcher_game_log(mlb_player_id, season)
        if r["gs"] >= 1 and r["date"] < day and (r["ip"] or 0) > 0
    ]
    if not prior_starts:
        return None
    window = prior_starts[-last_n:]
    ip_sum = sum(float(r["ip"]) for r in window)
    if ip_sum <= 0:
        return None
    # Recompute from pooled counting stats when possible.
    hr = sum(float(r["hr"]) for r in window)
    bb = sum(float(r["bb"]) for r in window)
    hbp = sum(float(r["hbp"]) for r in window)
    so = sum(float(r["so"]) for r in window)
    return _compute_fip_from_counting(hr, bb, hbp, so, ip_sum)


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
    # Prefer MLB's slate/official date over the UTC calendar day of gameDate.
    # Late ET/West Coast starts often have gameDate after midnight UTC.
    official = raw.get("officialDate")
    if official:
        game_day = str(official)[:10]
    else:
        game_day = str(raw["gameDate"][:10])
    return ScheduledGame(
        game_pk=int(raw["gamePk"]),
        game_date=game_day,
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
    hydrate_probable: bool = False,
) -> Iterator[ScheduledGame]:
    """Yield all regular-season games for a year (paginated by date in API response)."""
    params: dict[str, Any] = {
        "sportId": sport_id,
        "season": season,
        "gameType": game_type,
    }
    if hydrate_probable:
        params["hydrate"] = "probablePitcher(note),venue"
    data = _get("/schedule", params)
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


def fetch_play_by_play(game_pk: int) -> dict[str, Any]:
    """Play-by-play payload (allPlays, scoringPlays, etc.)."""
    return _get(f"/game/{game_pk}/playByPlay")


def fetch_live_feed(game_pk: int) -> dict[str, Any]:
    """
    Live feed with ``gameData`` and ``liveData`` sections.

    Tries the canonical ``/feed/live`` endpoint first. When unavailable (common
    for archived games), assembles a compatible structure from play-by-play,
    linescore, and schedule data.
    """
    try:
        return _get(f"/game/{game_pk}/feed/live")
    except MLBAPIError:
        pass

    pbp = fetch_play_by_play(game_pk)
    linescore = fetch_linescore(game_pk)
    scheduled = fetch_schedule_by_game_pk(game_pk, hydrate_probable=False)

    status: dict[str, Any]
    if scheduled is not None:
        status = {
            "abstractGameState": scheduled.status,
            "detailedState": scheduled.detailed_state,
        }
    else:
        status = {"abstractGameState": "Unknown", "detailedState": "Unknown"}

    return {
        "gameData": {
            "game": {"pk": game_pk},
            "status": status,
        },
        "liveData": {
            "plays": {
                "allPlays": pbp.get("allPlays") or [],
                "scoringPlays": pbp.get("scoringPlays") or [],
                "currentPlay": pbp.get("currentPlay"),
            },
            "linescore": linescore,
        },
    }


def _ip_to_float(ip_value: object) -> float:
    """Convert baseball IP notation (e.g. 108.2) to float innings."""
    if ip_value is None:
        return 0.0
    text = str(ip_value).strip()
    if not text or text.lower() in {"nan", "none", "-.--", ".--", "null"}:
        return 0.0
    try:
        if "." in text:
            whole_s, frac_s = text.split(".", 1)
            whole = int(whole_s or "0")
            frac = int(frac_s or "0")
            return float(whole) + (frac / 3.0)
        return float(text)
    except ValueError:
        return 0.0


def _safe_float(value: object) -> Optional[float]:
    """Parse MLB numeric fields; treat placeholders like '-.--' as missing."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:  # NaN
            return None
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "-.--", ".--", "null"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _compute_fip_from_counting(
    hr: float,
    bb: float,
    hbp: float,
    so: float,
    ip: float,
) -> Optional[float]:
    if ip <= 0:
        return None
    return ((13.0 * hr) + (3.0 * (bb + hbp)) - (2.0 * so)) / ip + _FIP_CONSTANT


def _kbb9_from_stat(stat: dict[str, Any]) -> Optional[float]:
    k9 = _safe_float(stat.get("strikeoutsPer9Inn"))
    bb9 = _safe_float(stat.get("walksPer9Inn"))
    if k9 is None or bb9 is None:
        # Fall back to raw counts when per-9 fields are absent/placeholders.
        ip = _ip_to_float(stat.get("inningsPitched"))
        if ip <= 0:
            return None
        so = _safe_float(stat.get("strikeOuts")) or 0.0
        bb = _safe_float(stat.get("baseOnBalls")) or 0.0
        return (so * 9.0 / ip) - (bb * 9.0 / ip)
    return k9 - bb9


def _fip_from_season_stat(stat: dict[str, Any]) -> Optional[float]:
    return _compute_fip_from_counting(
        _safe_float(stat.get("homeRuns")) or 0.0,
        _safe_float(stat.get("baseOnBalls")) or 0.0,
        _safe_float(stat.get("hitByPitch")) or 0.0,
        _safe_float(stat.get("strikeOuts")) or 0.0,
        _ip_to_float(stat.get("inningsPitched")),
    )


def prefetch_season_pitcher_stats(season: int) -> dict[str, int]:
    """
    Warm in-process FIP/xFIP and K-BB caches for one season.

    Uses two league-wide MLB Stats API calls:
    - season pitching (K/BB + FIP computed from counting stats)
    - sabermetrics pitching (official FIP/xFIP when available; overwrites)
    """
    season_i = int(season)
    filled_kbb9 = 0
    filled_fip = 0

    # Prefer the split with the most IP when a pitcher has multi-team rows.
    best_by_ip: dict[int, tuple[float, dict[str, Any]]] = {}
    for split in fetch_season_stat_splits(season_i, group="pitching"):
        player = split.get("player") or {}
        pid = player.get("id")
        if pid is None:
            continue
        stat = split.get("stat") or {}
        ip = _ip_to_float(stat.get("inningsPitched"))
        prev = best_by_ip.get(int(pid))
        if prev is None or ip >= prev[0]:
            best_by_ip[int(pid)] = (ip, stat)

    for pid, (_ip, stat) in best_by_ip.items():
        key = (pid, season_i)
        if key not in _pitcher_kbb9_cache:
            _pitcher_kbb9_cache[key] = _kbb9_from_stat(stat)
            filled_kbb9 += 1
        if key not in _pitcher_fip_cache:
            fip = _fip_from_season_stat(stat)
            _pitcher_fip_cache[key] = (fip, None)
            filled_fip += 1

    # Official sabermetrics FIP/xFIP overrides computed values when present.
    saber = _get(
        "/stats",
        {
            "stats": "sabermetrics",
            "group": "pitching",
            "season": season_i,
            "sportIds": 1,
            "limit": 2000,
            "playerPool": "all",
        },
    )
    saber_splits = list((saber.get("stats") or [{}])[0].get("splits") or [])
    for split in saber_splits:
        player = split.get("player") or {}
        pid = player.get("id")
        if pid is None:
            continue
        st = split.get("stat") or {}
        fip = _safe_float(st.get("fip"))
        if fip is None:
            continue
        xf = _safe_float(st.get("xfip"))
        key = (int(pid), season_i)
        had = key in _pitcher_fip_cache
        _pitcher_fip_cache[key] = (fip, xf)
        if not had:
            filled_fip += 1

    return {
        "pitchers_season": len(best_by_ip),
        "pitchers_sabermetrics": len(saber_splits),
        "filled_kbb9": filled_kbb9,
        "filled_fip": filled_fip,
    }


def fetch_pitcher_season_fip_xfip(
    mlb_player_id: int,
    season: int,
    *,
    as_of: Optional[str] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """
    Season pitching FIP/xFIP from MLB sabermetrics (full season) or counting
    stats through ``as_of`` (YYYY-MM-DD) when provided.
    """
    season_i = int(season)
    pid = int(mlb_player_id)
    if as_of:
        end = str(as_of)[:10]
        prefetch_as_of_pitcher_stats(season_i, end)
        key: tuple = (pid, season_i, end)
        if key in _pitcher_fip_cache:
            return _pitcher_fip_cache[key]
        _pitcher_fip_cache[key] = (None, None)
        return None, None

    key = (pid, season_i)
    if key in _pitcher_fip_cache:
        return _pitcher_fip_cache[key]
    try:
        j = _get(
            f"/people/{pid}/stats",
            {"stats": "sabermetrics", "group": "pitching", "season": season_i},
        )
        splits = j["stats"][0]["splits"]
        if not splits:
            _pitcher_fip_cache[key] = (None, None)
            return None, None
        st = splits[0]["stat"]
        fip = _safe_float(st.get("fip"))
        if fip is None:
            _pitcher_fip_cache[key] = (None, None)
            return None, None
        xf = _safe_float(st.get("xfip"))
        out = (fip, xf)
        _pitcher_fip_cache[key] = out
        return out
    except (KeyError, IndexError, TypeError, ValueError, MLBAPIError):
        _pitcher_fip_cache[key] = (None, None)
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


def fetch_pitcher_season_kbb9(
    mlb_player_id: int,
    season: int,
    *,
    as_of: Optional[str] = None,
) -> Optional[float]:
    """
    Return (K/9 - BB/9) from MLB pitching stats.

    With ``as_of``, uses YTD through that date (warmed via ``prefetch_as_of_pitcher_stats``).
    """
    season_i = int(season)
    pid = int(mlb_player_id)
    if as_of:
        end = str(as_of)[:10]
        prefetch_as_of_pitcher_stats(season_i, end)
        key: tuple = (pid, season_i, end)
        if key in _pitcher_kbb9_cache:
            return _pitcher_kbb9_cache[key]
        _pitcher_kbb9_cache[key] = None
        return None

    key = (pid, season_i)
    if key in _pitcher_kbb9_cache:
        return _pitcher_kbb9_cache[key]
    try:
        j = _get(
            f"/people/{pid}/stats",
            {"stats": "season", "group": "pitching", "season": season_i},
        )
        splits = j["stats"][0]["splits"]
        if not splits:
            _pitcher_kbb9_cache[key] = None
            return None
        st = splits[0]["stat"]
        val = _kbb9_from_stat(st)
        _pitcher_kbb9_cache[key] = val
        return val
    except (KeyError, IndexError, TypeError, ValueError, MLBAPIError):
        _pitcher_kbb9_cache[key] = None
        return None


def fetch_team_ops_vs_pitcher_hand(
    team_id: int,
    season: int,
    pitcher_hand: str,
    *,
    as_of: Optional[str] = None,
) -> Optional[float]:
    """
    Team OPS split by pitcher handedness from MLB team stats.
    pitcher_hand: 'R' or 'L'. (Uses sitCodes: 'vr'/'vl')

    When ``as_of`` is set, request ``byDateRange`` through that date when the API
    accepts it; otherwise fall back to full-season split.
    """
    ph = pitcher_hand.upper()
    if ph not in ("R", "L"):
        return None
    end = str(as_of)[:10] if as_of else None
    key = (int(team_id), int(season), ph, end or "season")
    if key in _team_ops_split_cache:
        return _team_ops_split_cache[key]
    # Legacy 3-tuple keys from older callers/tests.
    legacy_key = (int(team_id), int(season), ph)
    if end is None and legacy_key in _team_ops_split_cache:
        return _team_ops_split_cache[legacy_key]
    sit = "vr" if ph == "R" else "vl"
    params: Dict[str, Any] = {
        "season": int(season),
        "stats": "byDateRange" if end else "season",
        "group": "hitting",
        "sportId": 1,
        "sitCodes": sit,
    }
    if end:
        params["startDate"] = season_stats_window_start(int(season))
        params["endDate"] = end
    try:
        j = _get(f"/teams/{int(team_id)}/stats", params)
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
        # If byDateRange + sitCodes fails, fall back to season split once.
        if end:
            try:
                return fetch_team_ops_vs_pitcher_hand(team_id, season, pitcher_hand, as_of=None)
            except Exception:
                pass
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
