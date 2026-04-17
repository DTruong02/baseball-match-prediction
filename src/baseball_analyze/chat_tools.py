"""Tool functions for LLM chat (pure Python, no LLM SDK imports)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from baseball_analyze.mlb_client import (
    ScheduledGame,
    fetch_schedule_by_game_pk,
    fetch_schedule_for_date,
    fetch_teams,
)
from baseball_analyze.predict_core import build_feature_rows, predict_for_feature_rows

_TEAM_HINT_MAP: dict[str, str] | None = None


def resolve_date(text: str, *, tz: str = "America/New_York") -> str:
    """
    Resolve common date words to YYYY-MM-DD.

    This is intentionally small and deterministic so the LLM does not guess.
    """
    s = (text or "").strip().lower()
    if not s:
        raise ValueError("text must be a non-empty string")
    today = datetime.now(ZoneInfo(tz)).date()
    if s in {"today", "tonight"}:
        return today.isoformat()
    if s == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    if s == "yesterday":
        return (today - timedelta(days=1)).isoformat()

    # If the user gave a full ISO date, keep it.
    try:
        return datetime.fromisoformat(s).date().isoformat()
    except ValueError:
        pass

    # As a last resort, allow MM/DD/YYYY (common in US).
    try:
        dt = datetime.strptime(s, "%m/%d/%Y")
        return dt.date().isoformat()
    except ValueError as e:
        raise ValueError(f"Could not parse date: {text!r}") from e


def list_games_for_date(date: str) -> list[dict]:
    games = fetch_schedule_for_date(date)
    out: list[dict] = []
    for g in games:
        out.append(
            {
                "game_pk": g.game_pk,
                "game_date": g.game_date,
                "season": g.season,
                "status": g.status,
                "detailed_state": g.detailed_state,
                "home_abbrev": g.home_abbrev,
                "away_abbrev": g.away_abbrev,
                "home_team_id": g.home_team_id,
                "away_team_id": g.away_team_id,
            }
        )
    return out


def _build_team_hint_map() -> dict[str, str]:
    global _TEAM_HINT_MAP
    if _TEAM_HINT_MAP is not None:
        return _TEAM_HINT_MAP

    mp: dict[str, str] = {}
    for t in fetch_teams(1):
        ab = str(t.get("abbreviation") or "").strip()
        name = str(t.get("name") or "").strip()
        team_name = str(t.get("teamName") or "").strip()
        club = str(t.get("clubName") or "").strip()

        for k in (ab, name, team_name, club):
            if k:
                mp[k.casefold()] = ab

    _TEAM_HINT_MAP = mp
    return mp


def _normalize_team_hint(hint: str | None) -> str | None:
    if hint is None:
        return None
    s = hint.strip()
    if not s:
        return None
    mp = _build_team_hint_map()
    return mp.get(s.casefold(), s)


def find_games_on_date(
    *,
    date: str,
    away_team: str | None = None,
    home_team: str | None = None,
) -> list[dict]:
    """
    Find scheduled games on a date using loose matching.

    - If away_team/home_team look like full names ("Yankees"), we map to abbreviations.
    - Matching is case-insensitive substring match on abbreviations.
    """
    away = _normalize_team_hint(away_team)
    home = _normalize_team_hint(home_team)
    games = fetch_schedule_for_date(date)
    hits: list[ScheduledGame] = []
    for g in games:
        if away and away.casefold() not in g.away_abbrev.casefold():
            continue
        if home and home.casefold() not in g.home_abbrev.casefold():
            continue
        hits.append(g)
    return [asdict(g) for g in hits]


def predict_games(
    *,
    model_path: str,
    game_pks: list[int],
    cache_dir: str | None = None,
) -> list[dict]:
    """
    Predict P(home win) for one or more MLB gamePks.

    Returns structured rows; probabilities come only from the sklearn artifact.
    """
    games: list[ScheduledGame] = []
    missing: list[int] = []
    for pk in game_pks:
        g = fetch_schedule_by_game_pk(int(pk))
        if g is None:
            missing.append(int(pk))
            continue
        games.append(g)

    rows, notes_all = build_feature_rows(games, cache_dir=Path(cache_dir) if cache_dir else None)
    if not rows:
        return [
            {
                "error": "no_games",
                "missing_game_pks": missing,
                "notes": ["No games found (or all postponed/cancelled)."],
            }
        ]

    proba = predict_for_feature_rows(model_path=Path(model_path), rows=rows)

    notes_by_pk: dict[int, list[str]] = {pk: ns for pk, ns in notes_all}
    out: list[dict] = []
    for fr, p in zip(rows, proba):
        out.append(
            {
                "game_pk": fr.game_pk,
                "season": fr.season,
                "away_fg": fr.away_fg,
                "home_fg": fr.home_fg,
                "p_home_win": float(p),
                "notes": notes_by_pk.get(fr.game_pk, []),
            }
        )

    if missing:
        out.append({"warning": "missing_game_pks", "missing_game_pks": missing})
    return out

