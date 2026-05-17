"""Password reset token record.

Stores the token hash (NOT the token itself) plus its expiry, so issuing a
reset never leaks a usable secret if the row is read. Verification compares
the SHA-256 of the supplied token against the stored hash.

Stage 12 (v1.7): ``must_change_on_use`` flags tokens issued via
the terminal-OTP path so the post-reset login flow knows to
force a password change. Tokens issued via the normal email
path (or pre-Stage-12) default to False.
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base, TimestampMixin


class PasswordResetToken(Base, TimestampMixin):
    __tablename__ = "password_reset_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Stage 12 (v1.7) — set True for tokens issued by the
    # terminal-OTP path (email provider not configured). When
    # ``confirm_password_reset`` consumes such a token, it sets
    # the user's ``must_change_password`` flag so the login
    # flow forces a password change.
    must_change_on_use: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )

    @property
    def is_used(self) -> bool:
        return self.used_at is not None
