"""Update-check audit log.

One row per poll of the update feed. Lets us show "last checked X
minutes ago" in the UI and surface persistent feed failures (auth
expired, rate-limited, DNS gone). The newest row with ``ok=True`` is
also the source of truth for "is there an update available".

Rows accumulate but at ~one per hour the table stays small for years.
Stage 13 housekeeping may trim older rows; not needed now.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import Boolean, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base


class UpdateCheck(Base):
    __tablename__ = "update_checks"
    __table_args__ = (
        Index("ix_update_checks_checked_at", "checked_at"),
        Index("ix_update_checks_ok", "ok"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    checked_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Version on the feed at check time (may be older than what's installed
    # if the operator pinned a build; the comparison happens at use-site).
    latest_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Free-form changelog blob copied from the feed. The UI renders this
    # as plain text; rich rendering is up to Stage 13 polish.
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When ``ok=False``, the reason. When ``ok=True``, may carry feed-level
    # notes ("feed served stale cache") to surface in the UI.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The feed URL we contacted. Persisting it makes it possible to debug
    # "why did my updater suddenly start failing" after a config change.
    feed_url: Mapped[str] = mapped_column(String(512), nullable=False)
