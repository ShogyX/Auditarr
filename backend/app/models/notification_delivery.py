"""Notification delivery audit log.

One row per delivery attempt. The dispatcher creates the row in
``status='pending'``, the channel provider updates it to ``'sent'`` or
``'failed'`` on completion.

Retention is uncapped at this stage; Stage 13 housekeeping will add a
trim job that drops rows older than ~30 days (configurable). Until then
rows accumulate, but for typical home-lab volumes this is a few hundred
rows per month at most.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        Index("ix_notif_delivery_channel", "channel_id"),
        Index("ix_notif_delivery_status", "status"),
        Index("ix_notif_delivery_attempted_at", "attempted_at"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    channel_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("notification_channels.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Denormalized so the audit log survives channel deletion.
    channel_name: Mapped[str] = mapped_column(String(120), nullable=False)
    channel_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    # ``pending`` | ``sent`` | ``failed`` | ``skipped`` (below threshold)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Context that triggered the delivery (rule + file). Free-form so
    # different alert sources can encode their own audit detail.
    context: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    attempted_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
