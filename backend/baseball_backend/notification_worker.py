"""CLI entrypoint: deliver pending email notifications."""

from __future__ import annotations

import argparse
import logging
import time

from sqlalchemy import func, select

from baseball_backend.db.models import Notification, NotificationChannel, NotificationDeliveryStatus
from baseball_backend.db.session import get_session_factory
from baseball_backend.logging_config import configure_logging
from baseball_backend.services.notification_delivery import deliver_pending_emails
from baseball_backend.settings import get_settings
from baseball_backend.worker_status import record_notification_worker_heartbeat

logger = logging.getLogger(__name__)


def _pending_email_count(session) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.channel == NotificationChannel.EMAIL.value,
                Notification.status == NotificationDeliveryStatus.PENDING.value,
            )
        )
        or 0
    )


def _run_once(*, limit: int) -> dict[str, int]:
    started = time.perf_counter()
    session = get_session_factory()()
    try:
        pending_before = _pending_email_count(session)
        summary = deliver_pending_emails(session, limit=limit)
        pending_after = _pending_email_count(session)
        record_notification_worker_heartbeat(
            cycle_duration_seconds=time.perf_counter() - started,
            processed=summary["processed"],
            delivered=summary["delivered"],
            failed=summary["failed"],
            pending=pending_after,
        )
        summary["pending_before"] = pending_before
        summary["pending"] = pending_after
        return summary
    finally:
        session.close()


def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)
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
        logger.info(
            "Notification cycle complete",
            extra={
                "processed": summary["processed"],
                "delivered": summary["delivered"],
                "failed": summary["failed"],
                "pending": summary.get("pending"),
            },
        )
        return

    logger.info(
        "Starting notification worker",
        extra={"interval": args.interval, "limit": args.limit},
    )
    while True:
        summary = _run_once(limit=args.limit)
        if summary["processed"]:
            logger.info(
                "Notification cycle complete",
                extra={
                    "processed": summary["processed"],
                    "delivered": summary["delivered"],
                    "failed": summary["failed"],
                    "pending": summary.get("pending"),
                },
            )
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
