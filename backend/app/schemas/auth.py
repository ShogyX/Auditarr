"""Auth API request/response schemas."""

from __future__ import annotations

import datetime as _dt
import re

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# ── Permissive email validation (Stage 1 / L1) ────────────────────────
# Pydantic's ``EmailStr`` rejects hostname-only addresses such as
# ``admin@localhost`` because the underlying ``email-validator`` library
# requires a public-suffix TLD by default. Auditarr deployments that
# bootstrap an admin against the local Docker hostname end up with an
# email value that round-trips fine on the way IN (the bootstrap path
# stores it as a plain string column) but blows up on the way OUT when
# ``UserPublic`` tries to serialize it back into a response — every
# ``GET /api/v1/auth/me`` 500s.
#
# This validator accepts any non-empty ``local@host`` shape:
#   - non-empty local part
#   - exactly one ``@``
#   - non-empty host that contains at least one valid hostname character
#   - no leading or trailing whitespace
#
# It deliberately does NOT require a TLD, so ``admin@localhost`` works.
# It still rejects obvious garbage (empty string, missing ``@``, control
# characters, embedded whitespace). The strict ``EmailStr`` is kept for
# inbound registration where rejecting host-only addresses is the right
# call.
_PERMISSIVE_EMAIL_RE = re.compile(
    r"^[^\s@]+@[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$"
)


def _validate_permissive_email(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("email must be a string")
    if value != value.strip():
        raise ValueError("email must not have leading or trailing whitespace")
    if not value:
        raise ValueError("email must not be empty")
    if not _PERMISSIVE_EMAIL_RE.match(value):
        raise ValueError("email must be of the form local@host")
    return value


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login: str = Field(min_length=1, description="email or username")
    password: str = Field(min_length=1)


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Registration is the one path where we keep strict EmailStr.
    # An operator self-registering should be using a real address;
    # ``admin@localhost`` only exists as a bootstrap-time artifact.
    email: EmailStr
    username: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_.\-]+$")
    password: str = Field(min_length=12, max_length=256)
    full_name: str | None = Field(default=None, max_length=120)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str
    new_password: str = Field(min_length=12, max_length=256)


class ProfileUpdateRequest(BaseModel):
    """Body for ``PATCH /api/v1/auth/me``.

    All fields optional — the operator can update name without
    touching email, or vice versa. Empty strings are rejected
    for email (full_name empty string is a deliberate "clear" sentinel);
    omitted fields leave the existing value alone.

    The ``email`` field uses the permissive validator so an operator
    can keep their bootstrap-time ``admin@localhost`` address even when
    editing other profile fields (Stage 1 / L1).
    """

    model_config = ConfigDict(extra="forbid")

    email: str | None = None
    full_name: str | None = Field(default=None, max_length=120)
    # ``username`` is intentionally not editable here — usernames
    # appear in audit logs and changing them in-place would break
    # historical attribution. Operators rename via admin tooling.

    @field_validator("email", mode="after")
    @classmethod
    def _validate_email(cls, v: str | None) -> str | None:
        return _validate_permissive_email(v)


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class PasswordResetConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=8, max_length=512)
    new_password: str = Field(min_length=12, max_length=256)


class UserPublic(BaseModel):
    """Safe-to-return user representation.

    Uses the permissive email validator (Stage 1 / L1) so existing rows
    in the ``users`` table with hostname-only emails (most commonly the
    bootstrap admin's ``admin@localhost``) can be serialized without
    crashing the response. A stricter ``EmailStr`` would force a data
    migration; the permissive variant matches operator expectations.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    username: str
    full_name: str | None
    role: str
    is_active: bool
    is_verified: bool
    # Stage 12 (v1.7) — set True when the user has consumed
    # a terminal-OTP password reset and hasn't completed a
    # subsequent ``change_password`` call yet. The frontend's
    # post-login flow checks this and routes to the change-
    # password screen before the dashboard.
    must_change_password: bool = False
    created_at: _dt.datetime
    last_login_at: _dt.datetime | None

    @field_validator("email", mode="after")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        # On the response side, the value is never ``None`` (NOT NULL
        # column), but we route through the same helper so behavior
        # stays consistent.
        result = _validate_permissive_email(v)
        # Helper returns ``None`` only when given ``None``; we never
        # pass ``None`` here, so this assert documents the invariant.
        assert result is not None
        return result
