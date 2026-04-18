from __future__ import annotations

import pytest

from animica.bridge_banm.auth import hash_password, sign_token, verify_password, verify_token


def test_password_hash_round_trip() -> None:
    salt, digest = hash_password("secret-password")
    assert verify_password("secret-password", salt=salt, password_hash=digest)
    assert not verify_password("wrong-password", salt=salt, password_hash=digest)


def test_token_sign_verify_and_expiry() -> None:
    token = sign_token({"sub": "admin", "role": "admin"}, secret="test-secret", expires_in_seconds=2)
    payload = verify_token(token, secret="test-secret")
    assert payload["sub"] == "admin"
    assert payload["role"] == "admin"

    with pytest.raises(ValueError):
        verify_token(token, secret="wrong-secret")

