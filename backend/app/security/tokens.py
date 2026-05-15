"""JWT issuance and verification.

Two token types: ``access`` (short-lived, sent on every API call) and
``refresh`` (long-lived, used to mint new access tokens). The token ``typ``
claim is enforced on verification to prevent cross-use.

Tokens carry a ``jti`` (token id) and a ``ver`` (token version) claim. Token
version is bumped on every password change so all outstanding tokens for a
user are invalidated atomically.
"""

from __future__ import annotations

import datetime as _dt
import secrets
from dataclasses import dataclass
from typing import Any, Final, Literal

import jwt

from app.core.exceptions import AuthenticationError
from app.core.settings import Settings

ACCESS: Final = "access"
REFRESH: Final = "refresh"
RESET: Final = "reset"

TokenType = Literal["access", "refresh", "reset"]


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Decoded, validated token claims."""

    subject: str
    token_type: TokenType
    token_id: str
    token_version: int
    issued_at: _dt.datetime
    expires_at: _dt.datetime


class TokenService:
    """Encode + decode JWTs using settings-supplied secret + algorithm."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # ── Issue ─────────────────────────────────────────────────
    def issue_access(self, subject: str | int, *, token_version: int = 0) -> str:
        return self._issue(
            subject,
            token_type=ACCESS,
            ttl=_dt.timedelta(minutes=self._settings.access_token_ttl_minutes),
            token_version=token_version,
        )

    def issue_refresh(self, subject: str | int, *, token_version: int = 0) -> str:
        return self._issue(
            subject,
            token_type=REFRESH,
            ttl=_dt.timedelta(days=self._settings.refresh_token_ttl_days),
            token_version=token_version,
        )

    def issue_reset(
        self,
        subject: str | int,
        *,
        token_version: int = 0,
        ttl_minutes: int = 30,
    ) -> str:
        return self._issue(
            subject,
            token_type=RESET,
            ttl=_dt.timedelta(minutes=ttl_minutes),
            token_version=token_version,
        )

    def _issue(
        self,
        subject: str | int,
        *,
        token_type: TokenType,
        ttl: _dt.timedelta,
        token_version: int,
    ) -> str:
        now = _dt.datetime.now(_dt.UTC)
        payload: dict[str, Any] = {
            "sub": str(subject),
            "typ": token_type,
            "jti": secrets.token_urlsafe(16),
            "ver": token_version,
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int((now + ttl).timestamp()),
        }
        return jwt.encode(
            payload, self._settings.secret_key, algorithm=self._settings.jwt_algorithm
        )

    # ── Verify ────────────────────────────────────────────────
    def decode(self, token: str, *, expected_type: TokenType) -> TokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._settings.secret_key,
                algorithms=[self._settings.jwt_algorithm],
                options={"require": ["exp", "iat", "sub", "typ", "jti"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Token has expired") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("Invalid token") from exc

        if payload.get("typ") != expected_type:
            raise AuthenticationError(
                f"Wrong token type (expected {expected_type})"
            )
        return TokenClaims(
            subject=str(payload["sub"]),
            token_type=payload["typ"],
            token_id=payload["jti"],
            token_version=int(payload.get("ver", 0)),
            issued_at=_dt.datetime.fromtimestamp(payload["iat"], _dt.UTC),
            expires_at=_dt.datetime.fromtimestamp(payload["exp"], _dt.UTC),
        )
