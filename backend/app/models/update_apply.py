"""Update-apply audit log.

Captures every attempt to apply an update. Because Auditarr lives in a
container and can't ``docker compose pull`` itself, the apply is a
*request*: we write a sentinel file the host helper script watches for,
then poll a status file the helper writes back. Each end-to-end attempt
gets a row here so the UI can show what the helper did.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base


class UpdateApply(Base):
    __tablename__ = "update_applies"
    __table_args__ = (
        Index("ix_update_applies_started_at", "started_at"),
        Index("ix_update_applies_status", "status"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # ``requested`` — sentinel written, waiting for helper.
    # ``running``   — helper picked it up.
    # ``completed`` — helper reported success.
    # ``failed``    — helper reported error, or we timed out waiting.
    # ``rolled_back`` — operator triggered rollback after the fact.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="requested"
    )
    # What we asked for, and what we ended up on.
    from_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_version: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The user who clicked apply. Audit only — apply is admin-only at the
    # router level.
    triggered_by_user_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
