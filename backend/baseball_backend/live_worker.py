"""CLI entrypoint: poll MLB live feeds for in-progress games."""

from __future__ import annotations

import argparse
import time
from datetime import date

from baseball_backend.db.session import get_session_factory
from baseball_backend.services.live_ingestion import sync_live_games_for_date
from baseball_backend.services.live_rate_limit import PollRateLimiter
from baseball_backend.settings import get_settings


def _run_once(
    game_date: str,
    *,
    rate_limiter: PollRateLimiter,
) -> int:
    """Run one poll cycle. Returns the number of failed game syncs."""
    session = get_session_factory()()
    try:
        summaries = sync_live_games_for_date(
            session,
            game_date,
            rate_limiter=rate_limiter,
        )
        if not summaries:
            print(f"No live games to poll for {game_date}")
            return 0
        failures = 0
        for summary in summaries:
            if "error" in summary:
                failures += 1
                print(
                    f"game_pk={summary['game_pk']}: error={summary['error']}"
                )
            else:
                print(
                    f"game_pk={summary['game_pk']}: "
                    f"status={summary['status']} "
                    f"events_inserted={summary['events_inserted']}"
                )
        return failures
    finally:
        session.close()


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Poll MLB live feeds and persist game events."
    )
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Game date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single poll cycle and exit",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=settings.live_poll_interval_seconds,
        help="Seconds between poll cycles (default: from settings)",
    )
    parser.add_argument(
        "--game-delay",
        type=float,
        default=settings.live_poll_game_delay_seconds,
        help="Seconds to wait between games within a cycle",
    )
    args = parser.parse_args()

    min_request_interval = max(
        args.game_delay,
        settings.live_poll_min_request_interval_seconds,
    )
    rate_limiter = PollRateLimiter(min_request_interval)

    if args.once:
        _run_once(args.date, rate_limiter=rate_limiter)
        return

    print(
        f"Starting live worker for {args.date} "
        f"(interval={args.interval}s, "
        f"min_request_interval={min_request_interval}s)"
    )
    consecutive_failures = 0
    while True:
        failures = _run_once(args.date, rate_limiter=rate_limiter)
        if failures > 0:
            consecutive_failures += 1
            # Back off the next cycle when MLB is unhealthy.
            sleep_for = args.interval * (2 ** min(consecutive_failures - 1, 3))
        else:
            consecutive_failures = 0
            sleep_for = args.interval
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
