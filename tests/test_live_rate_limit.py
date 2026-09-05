"""Tests for live poll rate limiting."""

import time
from unittest.mock import patch

from baseball_backend.services.live_rate_limit import PollRateLimiter


def test_poll_rate_limiter_enforces_min_interval() -> None:
    limiter = PollRateLimiter(0.05)
    started = time.monotonic()
    limiter.wait()
    limiter.wait()
    elapsed = time.monotonic() - started
    assert elapsed >= 0.045


def test_poll_rate_limiter_zero_interval_is_noop() -> None:
    limiter = PollRateLimiter(0)
    with patch("baseball_backend.services.live_rate_limit.time.sleep") as mock_sleep:
        limiter.wait()
        limiter.wait()
    mock_sleep.assert_not_called()
