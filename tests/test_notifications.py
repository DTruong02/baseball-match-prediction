"""Tests for notification preferences, inbox, and async email delivery."""

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from baseball_backend.db.base import Base
from baseball_backend.db.models import (
    Notification,
    NotificationAlertType,
    NotificationChannel,
    NotificationDeliveryStatus,
    NotificationPreference,
    User,
)
from baseball_backend.db.session import get_db
from baseball_backend.main import app
from baseball_backend.services.notification_delivery import deliver_pending_emails
from baseball_backend.services.notification_service import enqueue_notification
from baseball_backend.settings import Settings


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    jsonb_columns: list = []
    for column in Notification.__table__.columns:
        if isinstance(column.type, JSONB):
            jsonb_columns.append(column)
            column.type = JSON()
    tables = [
        User.__table__,
        NotificationPreference.__table__,
        Notification.__table__,
    ]
    Base.metadata.create_all(bind=engine, tables=tables)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine, tables=tables)
        engine.dispose()
        for column in jsonb_columns:
            column.type = JSONB()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _auth_headers(client: TestClient) -> dict[str, str]:
    client.post(
        "/auth/register",
        json={"email": "fan@example.com", "password": "secretpass"},
    )
    login = client.post(
        "/auth/login",
        data={"username": "fan@example.com", "password": "secretpass"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_get_preferences_creates_defaults(client: TestClient) -> None:
    headers = _auth_headers(client)
    response = client.get("/notifications/preferences", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["in_app_enabled"] is True
    assert body["email_enabled"] is False
    assert body["notify_game_start"] is True
    assert body["wp_threshold_pct"] == 0.15


def test_update_preferences(client: TestClient) -> None:
    headers = _auth_headers(client)
    response = client.put(
        "/notifications/preferences",
        headers=headers,
        json={"email_enabled": True, "notify_game_final": False, "wp_threshold_pct": 0.2},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email_enabled"] is True
    assert body["notify_game_final"] is False
    assert body["wp_threshold_pct"] == 0.2


def test_enqueue_respects_channels_and_inbox(
    client: TestClient, db_session: Session
) -> None:
    headers = _auth_headers(client)
    client.put(
        "/notifications/preferences",
        headers=headers,
        json={"email_enabled": True},
    )
    user = db_session.query(User).one()
    created = enqueue_notification(
        db_session,
        user_id=user.id,
        alert_type=NotificationAlertType.GAME_START.value,
        title="Yankees about to start",
        body="First pitch in 15 minutes.",
        payload={"game_pk": 1},
    )
    assert len(created) == 2
    channels = {row.channel for row in created}
    assert channels == {
        NotificationChannel.IN_APP.value,
        NotificationChannel.EMAIL.value,
    }

    inbox = client.get("/notifications", headers=headers)
    assert inbox.status_code == 200
    items = inbox.json()
    assert len(items) == 1
    assert items[0]["title"] == "Yankees about to start"
    assert items[0]["read_at"] is None

    unread = client.get("/notifications/unread-count", headers=headers)
    assert unread.json()["count"] == 1

    mark = client.post(f"/notifications/{items[0]['id']}/read", headers=headers)
    assert mark.status_code == 200
    assert mark.json()["read_at"] is not None
    assert client.get("/notifications/unread-count", headers=headers).json()["count"] == 0


def test_enqueue_skips_disabled_alert_type(
    client: TestClient, db_session: Session
) -> None:
    headers = _auth_headers(client)
    client.put(
        "/notifications/preferences",
        headers=headers,
        json={"notify_new_prediction": False},
    )
    user = db_session.query(User).one()
    created = enqueue_notification(
        db_session,
        user_id=user.id,
        alert_type=NotificationAlertType.NEW_PREDICTION.value,
        title="New prediction",
        body="Home 62%",
    )
    assert created == []


def test_deliver_pending_emails_sends_and_marks(
    client: TestClient, db_session: Session
) -> None:
    headers = _auth_headers(client)
    client.put(
        "/notifications/preferences",
        headers=headers,
        json={"email_enabled": True, "in_app_enabled": False},
    )
    user = db_session.query(User).one()
    enqueue_notification(
        db_session,
        user_id=user.id,
        alert_type=NotificationAlertType.GAME_FINAL.value,
        title="Final",
        body="Yankees win 5-3",
    )
    settings = Settings(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_from="alerts@example.com",
        smtp_use_tls=True,
    )
    with patch(
        "baseball_backend.services.notification_delivery.send_email"
    ) as mock_send:
        summary = deliver_pending_emails(db_session, settings=settings)
    assert summary == {"processed": 1, "delivered": 1, "failed": 0}
    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["to_address"] == "fan@example.com"
    assert kwargs["subject"] == "Final"

    row = db_session.query(Notification).one()
    assert row.status == NotificationDeliveryStatus.DELIVERED.value
    assert row.delivered_at is not None


def test_deliver_pending_emails_retries_transient_smtp_errors(
    client: TestClient, db_session: Session
) -> None:
    headers = _auth_headers(client)
    client.put(
        "/notifications/preferences",
        headers=headers,
        json={"email_enabled": True, "in_app_enabled": False},
    )
    user = db_session.query(User).one()
    enqueue_notification(
        db_session,
        user_id=user.id,
        alert_type=NotificationAlertType.GAME_FINAL.value,
        title="Final",
        body="Yankees win 5-3",
    )
    settings = Settings(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_from="alerts@example.com",
        smtp_use_tls=True,
        notification_email_retries=2,
        notification_email_backoff_seconds=0.01,
    )
    with (
        patch(
            "baseball_backend.services.notification_delivery.send_email",
            side_effect=[RuntimeError("smtp blip"), None],
        ) as mock_send,
        patch("baseball_backend.services.notification_delivery.time.sleep") as mock_sleep,
    ):
        summary = deliver_pending_emails(db_session, settings=settings)
    assert summary == {"processed": 1, "delivered": 1, "failed": 0}
    assert mock_send.call_count == 2
    mock_sleep.assert_called_once()
    row = db_session.query(Notification).one()
    assert row.status == NotificationDeliveryStatus.DELIVERED.value


def test_deliver_pending_emails_marks_failed_without_smtp(
    client: TestClient, db_session: Session
) -> None:
    headers = _auth_headers(client)
    client.put(
        "/notifications/preferences",
        headers=headers,
        json={"email_enabled": True, "in_app_enabled": False},
    )
    user = db_session.query(User).one()
    enqueue_notification(
        db_session,
        user_id=user.id,
        alert_type=NotificationAlertType.HIGH_LEVERAGE.value,
        title="Leverage",
        body="Bases loaded",
    )
    settings = Settings(smtp_host="", smtp_from="")
    summary = deliver_pending_emails(db_session, settings=settings)
    assert summary["failed"] == 1
    row = db_session.query(Notification).one()
    assert row.status == NotificationDeliveryStatus.FAILED.value
    assert "SMTP" in (row.error or "")


def test_mark_all_read(client: TestClient, db_session: Session) -> None:
    headers = _auth_headers(client)
    user = db_session.query(User).one()
    for i in range(3):
        enqueue_notification(
            db_session,
            user_id=user.id,
            alert_type=NotificationAlertType.GAME_START.value,
            title=f"Alert {i}",
            body="body",
        )
    assert client.get("/notifications/unread-count", headers=headers).json()["count"] == 3
    response = client.post("/notifications/read-all", headers=headers)
    assert response.status_code == 200
    assert response.json()["count"] == 0
    assert client.get("/notifications/unread-count", headers=headers).json()["count"] == 0


def test_smtp_send_email_uses_starttls() -> None:
    from baseball_backend.services.email_sender import send_email

    settings = Settings(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user",
        smtp_password="pass",
        smtp_from="alerts@example.com",
        smtp_use_tls=True,
    )
    smtp = MagicMock()
    smtp.__enter__ = MagicMock(return_value=smtp)
    smtp.__exit__ = MagicMock(return_value=False)
    with patch(
        "baseball_backend.services.email_sender.smtplib.SMTP", return_value=smtp
    ) as smtp_cls:
        send_email(
            to_address="fan@example.com",
            subject="Hi",
            body="Hello",
            settings=settings,
        )
    smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=30)
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("user", "pass")
    smtp.send_message.assert_called_once()
