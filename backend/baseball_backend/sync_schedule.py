"""CLI entrypoint to sync MLB schedule into the database."""

from __future__ import annotations

import argparse
from datetime import date

from baseball_backend.db.session import get_session_factory
from baseball_backend.services.schedule_sync import sync_schedule_for_date


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync MLB schedule for a date into Postgres.")
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Game date in YYYY-MM-DD format (default: today)",
    )
    args = parser.parse_args()

    session = get_session_factory()()
    try:
        count = sync_schedule_for_date(session, args.date)
        print(f"Synced {count} game(s) for {args.date}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
