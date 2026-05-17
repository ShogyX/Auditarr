"""User model.

The ``token_version`` column is the single source of truth for invalidating
all outstanding tokens for a user — bumped on password change, lock, etc.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.security.permissions import Role
from app.storage.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(
        String(16),
        default=Role.USER.value,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Stage 12 (v1.7) — set True by ``confirm_password_reset``
    # when the consumed token had ``must_change_on_use=True``.
    # The terminal-OTP path issues such tokens; the login flow
    # surfaces the flag in the response so the frontend can
    # force a change-password before the user reaches the app.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    # Bumped to invalidate all outstanding tokens for this user atomically.
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    last_login_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def role_enum(self) -> Role:
        return Role(self.role)
