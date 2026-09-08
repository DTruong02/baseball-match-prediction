"""Deliver pending email notifications asynchronously."""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from baseball_backend.services.email_sender import EmailNotConfiguredError, send_email
from baseball_backend.services.notification_service import (
    get_user_email,
    list_pending_email_notifications,
    mark_email_delivered,
    mark_email_failed,
)
from baseball_backend.settings import Settings, get_settings


def deliver_pending_emails(
    db: Session,
    *,
    settings: Optional[Settings] = None,
    limit: int = 50,
) -> dict[str, int]:
    """Send pending email-channel notifications. Returns delivered/failed counts."""
    settings = settings or get_settings()
    pending = list_pending_email_notifications(db, limit=limit)
    delivered = 0
    failed = 0

    for notification in pending:
        to_address = get_user_email(db, notification.user_id)
        if not to_address:
            mark_email_failed(db, notification, "User email not found")
            failed += 1
            continue
        try:
            send_email(
                to_address=to_address,
                subject=notification.title,
                body=notification.body,
                settings=settings,
            )
            mark_email_delivered(db, notification)
            delivered += 1
        except EmailNotConfiguredError as exc:
            mark_email_failed(db, notification, str(exc))
            failed += 1
        except Exception as exc:  # noqa: BLE001 — persist failure, keep worker alive
            mark_email_failed(db, notification, f"{type(exc).__name__}: {exc}")
            failed += 1

    return {"processed": len(pending), "delivered": delivered, "failed": failed}
