"""Simple rate limiter for outbound MLB live polls."""

from __future__ import annotations

import threading
import time


class PollRateLimiter:
    """
    Enforce a minimum spacing between poll requests.

    Used by the live worker so multi-game cycles do not burst the MLB API.
    """

    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval = max(0.0, float(min_interval_seconds))
        self._lock = threading.Lock()
        self._last_request_at = 0.0

    def wait(self) -> None:
        """Block until the next request is allowed."""
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
