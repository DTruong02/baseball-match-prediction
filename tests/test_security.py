"""Unit tests for password hashing and JWT helpers."""

from datetime import timedelta

import pytest

from baseball_backend.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_hash_and_verify_password() -> None:
    hashed = hash_password("secretpass")
    assert hashed != "secretpass"
    assert verify_password("secretpass", hashed)
    assert not verify_password("wrong", hashed)


def test_create_and_decode_access_token() -> None:
    token = create_access_token(
        subject="fan@example.com",
        secret_key="test-secret",
        expires_delta=timedelta(minutes=30),
    )
    payload = decode_access_token(token, "test-secret")
    assert payload.sub == "fan@example.com"


def test_decode_access_token_rejects_invalid_token() -> None:
    with pytest.raises(ValueError, match="Invalid token"):
        decode_access_token("not-a-token", "test-secret")
