"""Simple on-disk cache for expensive season-table / DataFrame loads."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pickle
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")


def default_cache_dir() -> Path:
    return Path.cwd() / "cache"


def season_table_as_of_key(
    season: int,
    *,
    today: Optional[dt.date] = None,
    as_of: Optional[dt.date | str] = None,
) -> str:
    """
    Cache key fragment for season tables.

    When ``as_of`` is provided (game-day snapshot end date), that ISO date is the
    key so historical backtests can freeze YTD stats through that day.

    Otherwise: finished seasons use a stable ``final`` key; the calendar-current
    (or future) season keys by UTC date so mid-season refreshes pick up YTD stats.
    """
    if as_of is not None:
        if isinstance(as_of, str):
            return str(as_of)[:10]
        return as_of.isoformat()
    day = today or dt.datetime.now(dt.timezone.utc).date()
    if int(season) >= day.year:
        return day.isoformat()
    return "final"


def pregame_stats_as_of(game_date: str | dt.date) -> str:
    """End date for pregame features: day before the scheduled game (YYYY-MM-DD)."""
    if isinstance(game_date, str):
        day = dt.date.fromisoformat(str(game_date)[:10])
    else:
        day = game_date
    return (day - dt.timedelta(days=1)).isoformat()


def _key_path(cache_dir: Path, namespace: str, key: str) -> Path:
    safe = hashlib.sha256(key.encode()).hexdigest()[:24]
    d = cache_dir / namespace
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe}.pkl"


def load_or_compute(
    namespace: str,
    key_parts: dict[str, Any],
    compute: Callable[[], T],
    cache_dir: Path | None = None,
) -> T:
    """Load pickle from cache if present; otherwise compute and save.

    Corrupt or version-incompatible pickles (common after pandas upgrades) are
    discarded and recomputed.
    """
    base = cache_dir or default_cache_dir()
    key = json.dumps(key_parts, sort_keys=True, default=str)
    path = _key_path(base, namespace, key)
    if path.exists():
        try:
            with path.open("rb") as f:
                return pickle.load(f)
        except Exception:
            # Pandas StringDtype / ArrowDtype pickles often break across versions.
            try:
                path.unlink()
            except OSError:
                pass
    value = compute()
    with path.open("wb") as f:
        pickle.dump(value, f)
    return value
