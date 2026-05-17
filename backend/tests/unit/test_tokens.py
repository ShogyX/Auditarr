"""TokenService round-trip tests."""

from __future__ import annotations

import datetime as _dt

import pytest

from app.core.exceptions import AuthenticationError
from app.core.settings import Settings
from app.security import ACCESS, RESET, TokenService


@pytest.fixture
def tokens() -> TokenService:
    return TokenService(
        Settings(secret_key="x" * 32, access_token_ttl_minutes=15)
    )


def test_access_round_trip(tokens: TokenService) -> None:
    token = tokens.issue_access("user-1", token_version=3)
    claims = tokens.decode(token, expected_type=ACCESS)
    assert claims.subject == "user-1"
    assert claims.token_type == ACCESS
    assert claims.token_version == 3


def test_wrong_type_rejected(tokens: TokenService) -> None:
    refresh = tokens.issue_refresh("user-1")
    with pytest.raises(AuthenticationError):
        tokens.decode(refresh, expected_type=ACCESS)


def test_reset_token_supported(tokens: TokenService) -> None:
    token = tokens.issue_reset("user-1", ttl_minutes=5)
    claims = tokens.decode(token, expected_type=RESET)
    assert claims.token_type == RESET


def test_decode_rejects_garbage(tokens: TokenService) -> None:
    with pytest.raises(AuthenticationError):
        tokens.decode("not.a.jwt", expected_type=ACCESS)


def test_token_versions_are_independent(tokens: TokenService) -> None:
    a = tokens.issue_access("u", token_version=1)
    b = tokens.issue_access("u", token_version=2)
    assert tokens.decode(a, expected_type=ACCESS).token_version == 1
    assert tokens.decode(b, expected_type=ACCESS).token_version == 2


def test_expiry_in_the_future(tokens: TokenService) -> None:
    claims = tokens.decode(tokens.issue_access("u"), expected_type=ACCESS)
    assert claims.expires_at > _dt.datetime.now(_dt.UTC)
