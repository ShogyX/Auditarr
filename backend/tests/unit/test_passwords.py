"""argon2 password helper tests."""

from __future__ import annotations

import pytest

from app.security.passwords import hash_password, needs_rehash, verify_password


def test_hash_round_trip() -> None:
    encoded = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", encoded) is True
    assert verify_password("wrong password", encoded) is False


def test_hash_includes_parameters() -> None:
    encoded = hash_password("correct horse battery staple")
    # Argon2id encoded hashes carry their parameters explicitly.
    assert encoded.startswith("$argon2id$")


def test_empty_password_rejected() -> None:
    with pytest.raises(ValueError):
        hash_password("")


def test_invalid_hash_does_not_crash() -> None:
    assert verify_password("anything", "not-a-real-hash") is False
    assert needs_rehash("not-a-real-hash") is True
