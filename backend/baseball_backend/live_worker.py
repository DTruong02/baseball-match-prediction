"""CLI entrypoint: poll MLB live feeds for in-progress games."""

from __future__ import annotations

import argparse
import time
from datetime import date

from baseball_backend.db.session import get_session_factory
from baseball_backend.services.live_ingestion import sync_live_games_for_date
from baseball_backend.settings import get_settings


def _run_once(game_date: str, game_delay_seconds: float) -> None:
    session = get_session_factory()()
    try:
        summaries = sync_live_games_for_date(
            session,
            game_date,
            game_delay_seconds=game_delay_seconds,
        )
        if not summaries:
            print(f"No live games to poll for {game_date}")
            return
        for summary in summaries:
            if "error" in summary:
                print(
                    f"game_pk={summary['game_pk']}: error={summary['error']}"
                )
            else:
                print(
                    f"game_pk={summary['game_pk']}: "
                    f"status={summary['status']} "
                    f"events_inserted={summary['events_inserted']}"
                )
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

    if args.once:
        _run_once(args.date, args.game_delay)
        return

    print(
        f"Starting live worker for {args.date} "
        f"(interval={args.interval}s, game_delay={args.game_delay}s)"
    )
    while True:
        _run_once(args.date, args.game_delay)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
