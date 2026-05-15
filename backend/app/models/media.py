"""Media file model.

One row per file Auditarr knows about. Holds the denormalized output of
``ffprobe`` plus the engine's own classification and severity. ``path`` is
the unique key — moving a file outside Auditarr's view results in the
record being marked orphaned by the next scan.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base, TimestampMixin


class MediaFile(Base, TimestampMixin):
    __tablename__ = "media_files"
    __table_args__ = (
        Index("ix_media_files_library_category", "library_id", "category"),
        Index("ix_media_files_library_severity", "library_id", "severity"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    library_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("libraries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    path: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True, index=True)
    relative_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    extension: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mtime: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    inode: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Classification: media | subtitle | image | metadata | junk | unknown.
    category: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")

    # Severity computed by the rules engine (Stage 6). Default ``ok`` until
    # rules run; the column is denormalized for filterable dashboards.
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="ok", index=True)
    severity_rank: Mapped[int] = mapped_column(Integer, nullable=False, default=10)

    # Media-only convenience columns. Sourced from ffprobe; populated for
    # ``category == "media"`` files, nullable otherwise.
    container: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    bitrate_kbps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    video_codec: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    audio_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subtitle_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    framerate: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_subtitles: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    subtitle_languages: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    audio_languages: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    # Full ffprobe payload retained for rule extensions that need details we
    # didn't denormalize.
    probe: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    probe_failed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    probe_error: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Scan bookkeeping.
    last_scan_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    seen_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: _dt.datetime.now(_dt.UTC),
        nullable=False,
    )
    is_orphaned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )

    # Stage 27: quarantine state.
    #
    # Quarantining a file marks it as deliberately set aside — the
    # operator has decided the file is broken, suspicious, or
    # otherwise out of scope for normal automation. Distinct from
    # ``is_orphaned`` (which means "the scanner couldn't find it on
    # disk anymore") and from ``probe_failed`` (which is a probe-time
    # technical failure). Quarantined files stay in the database with
    # their metadata intact; they just get excluded from automation
    # by default. The state is restorable — see the
    # ``/unquarantine`` endpoint.
    quarantined: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    quarantined_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    quarantined_reason: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )

    # Stage 19 (audit follow-up): content hash + VirusTotal result.
    # The hash is computed at most once per (path, mtime) pair —
    # see ``app/services/file_hash.py::should_rehash``. The VT
    # result is a small JSON blob shaped roughly like:
    #   {"malicious": int, "suspicious": int, "harmless": int,
    #    "undetected": int, "permalink": str}
    # populated by ``app/services/virustotal.py`` when the global
    # ``virustotal_enabled`` setting is on AND we have a hash to
    # look up. Both columns are nullable; a NULL ``hash_sha256``
    # means "never hashed", and a NULL ``virustotal_result`` means
    # "never looked up OR lookup returned nothing useful".
    hash_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    hash_computed_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    virustotal_result: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )
    virustotal_checked_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
