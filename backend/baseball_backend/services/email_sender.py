"""SMTP email delivery for notification worker (stdlib only)."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from baseball_backend.settings import Settings


class EmailNotConfiguredError(RuntimeError):
    """Raised when SMTP settings are incomplete."""


def smtp_configured(settings: Settings) -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def send_email(
    *,
    to_address: str,
    subject: str,
    body: str,
    settings: Settings,
) -> None:
    """Send a plain-text email via SMTP.

    Uses ``SMTP_USER`` / ``SMTP_PASSWORD`` when set. TLS is enabled when
    ``SMTP_USE_TLS`` is true (default).
    """
    if not smtp_configured(settings):
        raise EmailNotConfiguredError(
            "SMTP is not configured (set SMTP_HOST and SMTP_FROM)"
        )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = to_address
    message.set_content(body)

    if settings.smtp_use_tls:
        context = ssl.create_default_context()
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password or "")
            smtp.send_message(message)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password or "")
            smtp.send_message(message)
