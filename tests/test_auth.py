"""Tests for JWT authentication endpoints."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from baseball_backend.db.base import Base
from baseball_backend.db.models import User
from baseball_backend.db.session import get_db
from baseball_backend.main import app


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[User.__table__])
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()


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


def test_register_creates_user(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json={"email": "fan@example.com", "password": "secretpass"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "fan@example.com"
    assert body["id"] == 1
    assert "created_at" in body


def test_register_rejects_duplicate_email(client: TestClient) -> None:
    payload = {"email": "fan@example.com", "password": "secretpass"}
    assert client.post("/auth/register", json=payload).status_code == 201
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"] == "Email already registered"


def test_login_returns_access_token(client: TestClient) -> None:
    client.post("/auth/register", json={"email": "fan@example.com", "password": "secretpass"})
    response = client.post(
        "/auth/login",
        data={"username": "fan@example.com", "password": "secretpass"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_login_rejects_invalid_credentials(client: TestClient) -> None:
    client.post("/auth/register", json={"email": "fan@example.com", "password": "secretpass"})
    response = client.post(
        "/auth/login",
        data={"username": "fan@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"


def test_me_requires_authentication(client: TestClient) -> None:
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_me_returns_current_user(client: TestClient) -> None:
    client.post("/auth/register", json={"email": "fan@example.com", "password": "secretpass"})
    login = client.post(
        "/auth/login",
        data={"username": "fan@example.com", "password": "secretpass"},
    )
    token = login.json()["access_token"]
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["email"] == "fan@example.com"
