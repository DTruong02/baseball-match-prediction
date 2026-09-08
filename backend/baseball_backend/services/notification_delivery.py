"""Deliver pending email notifications asynchronously."""

from __future__ import annotations

import logging
import time
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

logger = logging.getLogger(__name__)


def _send_with_retries(
    *,
    to_address: str,
    subject: str,
    body: str,
    settings: Settings,
) -> None:
    """Send email with exponential backoff on transient SMTP/network errors."""
    retries = max(0, int(settings.notification_email_retries))
    backoff = max(0.0, float(settings.notification_email_backoff_seconds))
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            send_email(
                to_address=to_address,
                subject=subject,
                body=body,
                settings=settings,
            )
            return
        except EmailNotConfiguredError:
            raise
        except Exception as exc:  # noqa: BLE001 — classify below
            last_exc = exc
            if attempt >= retries:
                break
            delay = backoff * (2**attempt)
            logger.warning(
                "Email send failed; retrying",
                extra={
                    "attempt": attempt + 1,
                    "retries": retries,
                    "delay_seconds": delay,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            if delay > 0:
                time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def deliver_pending_emails(
    db: Session,
    *,
    settings: Optional[Settings] = None,
    limit: int = 50,
) -> dict[str, int]:
    """Send pending email-channel notifications. Returns delivered/failed counts.

    Each pending row is processed at most once per cycle (idempotent relative to
    status transitions). Transient SMTP failures are retried in-process before
    the row is marked failed.
    """
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
            _send_with_retries(
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
