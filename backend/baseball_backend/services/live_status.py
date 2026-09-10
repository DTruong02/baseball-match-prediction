"""Shared helpers for which games the live worker tracks."""

from __future__ import annotations

LIVE_DETAILED_STATES = frozenset(
    {
        "In Progress",
        "Delayed",
        "Delayed Start",
        "Manager Challenge",
        "Suspended",
        "Warmup",
    }
)

FINAL_DETAILED_STATES = frozenset({"Final", "Game Over", "Completed Early"})


def is_live_tracking_status(status: str, detailed_state: str) -> bool:
    """True when the live worker is expected to keep a fresh Redis snapshot."""
    if status == "Live":
        return True
    return detailed_state in LIVE_DETAILED_STATES
