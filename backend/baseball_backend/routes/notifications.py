"""Notification preference and in-app inbox routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from baseball_backend.db.models import User
from baseball_backend.db.session import get_db
from baseball_backend.deps import get_current_user
from baseball_backend.schemas import (
    NotificationPreferenceRead,
    NotificationPreferenceUpdate,
    NotificationRead,
    NotificationUnreadCount,
)
from baseball_backend.services import notification_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _prefs_to_read(prefs) -> NotificationPreferenceRead:
    return NotificationPreferenceRead.model_validate(prefs)


@router.get("/preferences", response_model=NotificationPreferenceRead)
def get_preferences(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificationPreferenceRead:
    prefs = notification_service.get_or_create_preferences(db, current_user.id)
    return _prefs_to_read(prefs)


@router.put("/preferences", response_model=NotificationPreferenceRead)
def put_preferences(
    body: NotificationPreferenceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificationPreferenceRead:
    prefs = notification_service.update_preferences(
        db,
        current_user.id,
        **body.model_dump(exclude_unset=True),
    )
    return _prefs_to_read(prefs)


@router.get("", response_model=list[NotificationRead])
def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[NotificationRead]:
    rows = notification_service.list_in_app_notifications(
        db,
        current_user.id,
        unread_only=unread_only,
        limit=limit,
    )
    return [NotificationRead.model_validate(row) for row in rows]


@router.get("/unread-count", response_model=NotificationUnreadCount)
def unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificationUnreadCount:
    count = notification_service.count_unread_in_app(db, current_user.id)
    return NotificationUnreadCount(count=count)


@router.post("/read-all", response_model=NotificationUnreadCount)
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificationUnreadCount:
    notification_service.mark_all_in_app_read(db, current_user.id)
    return NotificationUnreadCount(count=0)


@router.post("/{notification_id}/read", response_model=NotificationRead)
def mark_read(
    notification_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> NotificationRead:
    notification = notification_service.mark_notification_read(
        db, current_user.id, notification_id
    )
    if notification is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )
    return NotificationRead.model_validate(notification)
