"""CLI entrypoint to sync MLB schedule into the database."""

from __future__ import annotations

import argparse
import sys
from datetime import date

from baseball_backend.db.session import get_session_factory
from baseball_backend.services.schedule_sync import (
    sync_schedule_for_date,
    sync_schedule_for_date_range,
    sync_schedule_for_season,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Sync MLB schedule into Postgres. "
            "Use --date for a single day, --season for a full regular season, "
            "or --from/--to for a calendar range (fetches overlapping seasons once)."
        ),
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Single game date YYYY-MM-DD (default: today when no other mode is set)",
    )
    parser.add_argument(
        "--season",
        type=int,
        default=None,
        help="Sync an entire regular season (e.g. 2025) in one MLB schedule fetch",
    )
    parser.add_argument(
        "--from",
        dest="date_from",
        default=None,
        help="Range start YYYY-MM-DD (requires --to)",
    )
    parser.add_argument(
        "--to",
        dest="date_to",
        default=None,
        help="Range end YYYY-MM-DD (requires --from)",
    )
    parser.add_argument(
        "--alerts",
        action="store_true",
        help=(
            "Evaluate start/final follower alerts during --season/--from/--to "
            "(off by default for bulk backfills; single --date always evaluates alerts)"
        ),
    )
    parser.add_argument(
        "--game-type",
        default="R",
        help="MLB schedule gameType for season/range sync (default: R = regular season)",
    )
    args = parser.parse_args()

    modes = [
        args.season is not None,
        args.date_from is not None or args.date_to is not None,
        args.date is not None,
    ]
    if sum(1 for active in modes if active) > 1:
        parser.error("Use only one of --date, --season, or --from/--to")

    if args.date_from is not None or args.date_to is not None:
        if args.date_from is None or args.date_to is None:
            parser.error("--from and --to must be used together")
        mode = "range"
    elif args.season is not None:
        mode = "season"
    else:
        mode = "date"
        if args.date is None:
            args.date = date.today().isoformat()

    session = get_session_factory()()
    try:
        if mode == "season":
            count = sync_schedule_for_season(
                session,
                args.season,
                evaluate_alerts=args.alerts,
                game_type=args.game_type,
            )
            print(f"Synced {count} game(s) for season {args.season}")
        elif mode == "range":
            count = sync_schedule_for_date_range(
                session,
                args.date_from,
                args.date_to,
                evaluate_alerts=args.alerts,
                game_type=args.game_type,
            )
            print(f"Synced {count} game(s) for {args.date_from} .. {args.date_to}")
        else:
            count = sync_schedule_for_date(session, args.date, evaluate_alerts=True)
            print(f"Synced {count} game(s) for {args.date}")
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
