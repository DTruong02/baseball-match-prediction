"""Shared filters for mid-season / partial-season training samples."""

from __future__ import annotations

import datetime as dt
from typing import Optional, Sequence

COMPLETED_DETAILED_STATES = frozenset({"Final", "Completed Early"})


def parse_iso_date(value: str, *, field_name: str = "date") -> dt.date:
    text = value.strip()
    try:
        return dt.date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be YYYY-MM-DD, got {value!r}") from exc


def coerce_optional_iso_date(value: object, *, field_name: str) -> Optional[str]:
    """Normalize optional date config to YYYY-MM-DD or None."""
    if value is None:
        return None
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        return parse_iso_date(text, field_name=field_name).isoformat()
    raise TypeError(f"{field_name} must be a date string or date, got {type(value).__name__}")


def is_completed_game(detailed_state: str) -> bool:
    return detailed_state in COMPLETED_DETAILED_STATES


def include_game_date(game_date: str, *, through_date: Optional[str] = None) -> bool:
    """Return True when the game date should be kept in the training pool."""
    if through_date is None:
        return True
    return game_date[:10] <= through_date


def resolve_split_masks(
    *,
    seasons: Sequence[int],
    game_dates: Sequence[str],
    val_seasons: Sequence[int],
    val_from_date: Optional[str],
) -> tuple[str, object]:
    """
    Build a boolean validation mask.

    Precedence:
    1. ``val_from_date`` → games on/after that date (``time_date``)
    2. ``val_seasons`` → seasons in that set (``time``)
    3. caller handles random split when both are empty

    Returns ``(split_type, val_mask_or_None)``.
    """
    import numpy as np

    n = len(seasons)
    if len(game_dates) != n:
        raise ValueError("seasons and game_dates must be the same length")

    if val_from_date:
        val_mask = np.array(
            [gd[:10] >= val_from_date for gd in game_dates],
            dtype=bool,
        )
        if not val_mask.any():
            raise RuntimeError(
                f"val_from_date {val_from_date!r} produced 0 validation rows. "
                "Check through_date / seasons / finished games."
            )
        if val_mask.all():
            raise RuntimeError(
                f"val_from_date {val_from_date!r} captured all rows; no training rows left."
            )
        return "time_date", val_mask

    if val_seasons:
        val_set = set(val_seasons)
        val_mask = np.array([int(s) in val_set for s in seasons], dtype=bool)
        if not val_mask.any():
            raise RuntimeError(
                f"val_seasons {list(val_seasons)} produced 0 validation rows. Check seasons."
            )
        if val_mask.all():
            raise RuntimeError(
                f"val_seasons {list(val_seasons)} captured all rows; no training rows left."
            )
        return "time", val_mask

    return "random", None
