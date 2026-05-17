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
        # Stage 06 (v1.7): the built-in "Probe failed" rule
        # matches on ``probe_failed = True``, and the rule engine
        # scans every library file on each pass. An index keeps
        # the predicate cheap as libraries scale into the millions.
        Index("ix_media_files_probe_failed", "probe_failed"),
        # Stage 06 (v1.7): the built-in "VirusTotal non-clean"
        # rule matches on ``vt_status in ('malicious', 'suspicious')``.
        # Index keeps the predicate selective; the column is
        # nullable (most rows have no VT result) so most index
        # pages are tiny.
        Index("ix_media_files_vt_status", "vt_status"),
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

    # Stage 27 introduced quarantine columns (quarantined,
    # quarantined_at, quarantined_reason). Stage 05 (v1.7) removed
    # them — "delete means delete" (Section A.0). A file is either
    # in the library or it's in ``data_dir/trash/`` after a rule
    # deleted it; there's no intermediate "quarantined" state on
    # the row. The 0015 migration drops the columns and rewrites
    # any persisted rule definitions that referenced
    # ``type: "quarantine"`` to ``type: "delete"``.

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
    # Stage 06 (v1.7): VT scan status as a denormalised column.
    # Per addendum B.4, ``vt_status`` is a string column populated
    # by the VT plugin (Stage 10 wires the actual lookup) with one
    # of the canonical values defined in
    # ``app.rules.schema.VT_STATUS_VALUES``: "clean", "malicious",
    # "suspicious", "not_found", "error". NULL means "never
    # looked up" — distinct from ``not_found`` (which means "VT
    # said it doesn't know this hash"). The column exists in
    # Stage 06 even though the populating code arrives in Stage
    # 10 so the built-in "VirusTotal non-clean" rule has
    # somewhere to look. Indexed because the rule engine filters
    # on it every evaluation pass.
    vt_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
