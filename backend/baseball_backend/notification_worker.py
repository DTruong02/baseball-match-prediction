"""CLI entrypoint: deliver pending email notifications."""

from __future__ import annotations

import argparse
import time

from baseball_backend.db.session import get_session_factory
from baseball_backend.services.notification_delivery import deliver_pending_emails
from baseball_backend.settings import get_settings


def _run_once(*, limit: int) -> dict[str, int]:
    session = get_session_factory()()
    try:
        return deliver_pending_emails(session, limit=limit)
    finally:
        session.close()


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Deliver pending email notifications via SMTP."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one batch and exit",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=settings.notification_poll_interval_seconds,
        help="Seconds between delivery cycles (default: from settings)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max pending emails per cycle",
    )
    args = parser.parse_args()

    if args.once:
        summary = _run_once(limit=args.limit)
        print(
            f"processed={summary['processed']} "
            f"delivered={summary['delivered']} "
            f"failed={summary['failed']}"
        )
        return

    print(
        f"Starting notification worker "
        f"(interval={args.interval}s, limit={args.limit})"
    )
    while True:
        summary = _run_once(limit=args.limit)
        if summary["processed"]:
            print(
                f"processed={summary['processed']} "
                f"delivered={summary['delivered']} "
                f"failed={summary['failed']}"
            )
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
