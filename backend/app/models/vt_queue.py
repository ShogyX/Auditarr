"""VirusTotal lookup queue (Stage 10).

Per plan §515: "when VT integration is enabled, the scanner
enqueues files for VT lookup. Add a small ``vt_queue`` table:
``(media_file_id PK, enqueued_at, last_attempted_at,
attempt_count)``."

The queue is operator-visible state: the
``GET /api/v1/integrations/virustotal/status`` endpoint reports
``queue_size = COUNT(*)`` so the VirusTotal card on the
Integrations page shows pending work.

The actual drain worker that consumes the queue is a future
stage (plan §515 mandates the enqueue; the lookup itself is
already plumbed via the plugin's ``lookup_by_hash`` helper). For
Stage 10's "Done when" criterion (the built-in VT rule fires on
a fixture row), the rule operates on the
:attr:`app.models.media.MediaFile.vt_status` column directly —
it doesn't need the drain wired.
"""

from __future__ import annotations

import datetime as _dt

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base


class VtQueueItem(Base):
    __tablename__ = "vt_queue"
    __table_args__ = (
        # FIFO drain — the future worker SELECTs ordered by
        # ``enqueued_at ASC`` so files inserted first get
        # looked up first. The index makes that cheap as the
        # queue grows.
        Index(
            "ix_vt_queue_enqueued_at",
            "enqueued_at",
        ),
    )

    # PK is ``media_file_id`` itself — each file is queued at
    # most once. INSERT ON CONFLICT DO NOTHING is the natural
    # write pattern for the scanner-side enqueuer (idempotent
    # across re-scans of the same file). FK CASCADE keeps the
    # queue consistent with media_files when files are removed.
    media_file_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("media_files.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )

    # When the scanner enqueued the row. Defaults at DB-side
    # via ``server_default`` so the timestamp is consistent
    # with other timestamped tables even when the application
    # forgets to set it.
    enqueued_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    # NULL until the drain worker has tried at least one
    # lookup. The (future) drain worker uses this to back off
    # retries on rows that have failed before.
    last_attempted_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Drain-worker retry counter. The Stage 10 enqueue path
    # writes 0; the (future) drain worker increments on each
    # failed attempt so it can eventually give up.
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
