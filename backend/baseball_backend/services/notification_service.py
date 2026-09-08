"""Notification preference lookups, enqueue, and in-app read helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from baseball_backend.db.models import (
    Notification,
    NotificationAlertType,
    NotificationChannel,
    NotificationDeliveryStatus,
    NotificationPreference,
    User,
)

_ALERT_PREF_ATTR = {
    NotificationAlertType.GAME_START.value: "notify_game_start",
    NotificationAlertType.WP_THRESHOLD.value: "notify_wp_threshold",
    NotificationAlertType.HIGH_LEVERAGE.value: "notify_high_leverage",
    NotificationAlertType.GAME_FINAL.value: "notify_game_final",
    NotificationAlertType.NEW_PREDICTION.value: "notify_new_prediction",
}


def get_or_create_preferences(db: Session, user_id: int) -> NotificationPreference:
    prefs = db.scalar(
        select(NotificationPreference).where(NotificationPreference.user_id == user_id)
    )
    if prefs is not None:
        return prefs
    prefs = NotificationPreference(user_id=user_id)
    db.add(prefs)
    db.commit()
    db.refresh(prefs)
    return prefs


def update_preferences(
    db: Session,
    user_id: int,
    *,
    in_app_enabled: Optional[bool] = None,
    email_enabled: Optional[bool] = None,
    notify_game_start: Optional[bool] = None,
    notify_wp_threshold: Optional[bool] = None,
    notify_high_leverage: Optional[bool] = None,
    notify_game_final: Optional[bool] = None,
    notify_new_prediction: Optional[bool] = None,
    wp_threshold_pct: Optional[float] = None,
) -> NotificationPreference:
    prefs = get_or_create_preferences(db, user_id)
    if in_app_enabled is not None:
        prefs.in_app_enabled = in_app_enabled
    if email_enabled is not None:
        prefs.email_enabled = email_enabled
    if notify_game_start is not None:
        prefs.notify_game_start = notify_game_start
    if notify_wp_threshold is not None:
        prefs.notify_wp_threshold = notify_wp_threshold
    if notify_high_leverage is not None:
        prefs.notify_high_leverage = notify_high_leverage
    if notify_game_final is not None:
        prefs.notify_game_final = notify_game_final
    if notify_new_prediction is not None:
        prefs.notify_new_prediction = notify_new_prediction
    if wp_threshold_pct is not None:
        prefs.wp_threshold_pct = wp_threshold_pct
    db.commit()
    db.refresh(prefs)
    return prefs


def _alert_type_enabled(prefs: NotificationPreference, alert_type: str) -> bool:
    attr = _ALERT_PREF_ATTR.get(alert_type)
    if attr is None:
        return True
    return bool(getattr(prefs, attr))


def enqueue_notification(
    db: Session,
    *,
    user_id: int,
    alert_type: str,
    title: str,
    body: str,
    payload: Optional[dict[str, Any]] = None,
) -> list[Notification]:
    """Create in-app and/or pending email rows according to user preferences.

    In-app rows are marked delivered immediately. Email rows stay pending for the
    notification worker to send asynchronously.
    """
    prefs = get_or_create_preferences(db, user_id)
    if not _alert_type_enabled(prefs, alert_type):
        return []

    now = datetime.now(timezone.utc)
    created: list[Notification] = []

    if prefs.in_app_enabled:
        in_app = Notification(
            user_id=user_id,
            channel=NotificationChannel.IN_APP.value,
            alert_type=alert_type,
            title=title,
            body=body,
            payload=payload,
            status=NotificationDeliveryStatus.DELIVERED.value,
            delivered_at=now,
        )
        db.add(in_app)
        created.append(in_app)

    if prefs.email_enabled:
        email = Notification(
            user_id=user_id,
            channel=NotificationChannel.EMAIL.value,
            alert_type=alert_type,
            title=title,
            body=body,
            payload=payload,
            status=NotificationDeliveryStatus.PENDING.value,
        )
        db.add(email)
        created.append(email)

    if created:
        db.commit()
        for row in created:
            db.refresh(row)
    return created


def list_in_app_notifications(
    db: Session,
    user_id: int,
    *,
    unread_only: bool = False,
    limit: int = 50,
) -> list[Notification]:
    query = (
        select(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.channel == NotificationChannel.IN_APP.value,
        )
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
    )
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    return list(db.scalars(query).all())


def count_unread_in_app(db: Session, user_id: int) -> int:
    from sqlalchemy import func

    return int(
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.channel == NotificationChannel.IN_APP.value,
                Notification.read_at.is_(None),
            )
        )
        or 0
    )


def mark_notification_read(
    db: Session, user_id: int, notification_id: int
) -> Optional[Notification]:
    notification = db.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
            Notification.channel == NotificationChannel.IN_APP.value,
        )
    )
    if notification is None:
        return None
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(notification)
    return notification


def mark_all_in_app_read(db: Session, user_id: int) -> int:
    now = datetime.now(timezone.utc)
    notifications = list(
        db.scalars(
            select(Notification).where(
                Notification.user_id == user_id,
                Notification.channel == NotificationChannel.IN_APP.value,
                Notification.read_at.is_(None),
            )
        ).all()
    )
    for notification in notifications:
        notification.read_at = now
    if notifications:
        db.commit()
    return len(notifications)


def list_pending_email_notifications(
    db: Session, *, limit: int = 50
) -> list[Notification]:
    return list(
        db.scalars(
            select(Notification)
            .where(
                Notification.channel == NotificationChannel.EMAIL.value,
                Notification.status == NotificationDeliveryStatus.PENDING.value,
            )
            .order_by(Notification.created_at.asc(), Notification.id.asc())
            .limit(limit)
        ).all()
    )


def mark_email_delivered(db: Session, notification: Notification) -> None:
    notification.status = NotificationDeliveryStatus.DELIVERED.value
    notification.delivered_at = datetime.now(timezone.utc)
    notification.error = None
    db.commit()


def mark_email_failed(db: Session, notification: Notification, error: str) -> None:
    notification.status = NotificationDeliveryStatus.FAILED.value
    notification.error = error[:2000]
    db.commit()


def get_user_email(db: Session, user_id: int) -> Optional[str]:
    user = db.get(User, user_id)
    return user.email if user is not None else None
