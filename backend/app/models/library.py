"""Library model.

A library is a named root directory that Auditarr scans. Multiple libraries
can be defined; each has its own scan schedule and optional integration
link (Plex section, Sonarr root folder, etc.).
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base, TimestampMixin


class Library(Base, TimestampMixin):
    __tablename__ = "libraries"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    root_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(16),
        default="movies",
        nullable=False,
        doc="movies | tv | music | mixed",
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Optional link to an upstream integration record (Plex section id,
    # Sonarr root folder id, etc.). Free-form JSON keeps this evolvable.
    integration_link: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Scan scheduling.
    scan_interval_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_scan_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_scan_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_scan_file_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
