"""Integration model.

A configured connection to an external service (Plex, Sonarr, Radarr, etc.).
The integration ``kind`` matches the id of the integration plugin that
provides the implementation; ``config`` holds non-secret options;
``secrets`` holds encrypted credentials. Health state is denormalized for
the dashboard.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base, TimestampMixin


class Integration(Base, TimestampMixin):
    __tablename__ = "integrations"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    name: Mapped[str] = mapped_column(
        String(120), unique=True, index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
        doc="Integration plugin id, e.g. 'plex', 'sonarr', 'radarr'",
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Public options — base URL, library section ids, polling interval, etc.
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # AES-GCM ciphertext blob (base64). NULL when no secrets needed.
    secrets_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Last healthcheck result, surfaced on the dashboard. ``status`` is one of
    # ``unknown | ok | degraded | error``. Plugins write this on every poll.
    health_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown"
    )
    health_detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    health_checked_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Soft poll cadence — Stage 7 scheduler uses this. ``0`` disables.
    poll_interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=300
    )

    # Stage 17 (audit follow-up): snapshot of libraries discovered
    # from the upstream at integration-create time (or via a manual
    # rediscover). Used by the Path Mappings panel to surface the
    # gap between what the integration says it has and what the
    # operator has configured a mapping for. NEVER auto-applied —
    # the operator drives every Add/Remove/Update.
    #
    # Shape: ``list[{library_id, label, upstream_path, discovered_at}]``
    # ``None`` = never discovered (legacy rows; the panel offers an
    # admin-only "Discover now" button to populate).
    discovered_paths: Mapped[list[dict] | None] = mapped_column(
        JSON, nullable=True
    )

    # Stage 19 (audit follow-up): per-integration webhook secret.
    # Encrypted using the same SecretBox as ``secrets_ciphertext``.
    # Generated on demand via ``POST /integrations/{id}/webhook-secret``
    # — the plaintext is returned to the operator ONCE in that
    # response and never again (the row holds only the ciphertext).
    # ``None`` means "no webhook secret configured" — incoming
    # webhooks to this integration's id will 401.
    webhook_secret_ciphertext: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
