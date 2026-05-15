"""Notification channel model.

An operator-configured destination for alerts. Each row carries the
channel kind (``email``, ``webhook``, ``discord``, ``slack``,
``apprise``, or plugin-registered), a free-form config dict, encrypted
secrets (API tokens, SMTP passwords), and a per-channel severity
threshold so noisy channels can opt out of info/warn-level notifications
without disabling rules.

The config + secrets pattern mirrors :class:`Integration` deliberately:
both use the same secret-box wire format and the same provider Protocol
shape. Operators familiar with integrations get a free conceptual model
for channels.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base, TimestampMixin


class NotificationChannel(Base, TimestampMixin):
    __tablename__ = "notification_channels"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(
        String(120), unique=True, index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Public, schema-validated configuration.
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Encrypted secrets, base64(ver || nonce || ciphertext+tag).
    secrets_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Severity threshold: channel only fires when the rule's severity
    # rank >= this value. Default 40 (warn). 0 = receive everything.
    min_severity_rank: Mapped[int] = mapped_column(
        Integer, default=40, nullable=False
    )
    # Most-recent successful/failed delivery — surfaced in the UI.
    last_delivery_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    last_delivery_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_delivery_error: Mapped[str | None] = mapped_column(Text, nullable=True)
