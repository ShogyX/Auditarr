"""Global path mappings (Stage 5 / audit follow-up).

Per-integration path mappings (stored on ``Integration.config.path_mappings``)
already exist. Stage 5 adds a second layer: a global table of mappings
that apply across **every** library and integration, not just one.

Rationale: operators with multiple integrations pointing at the same
underlying media (Plex + Sonarr + Radarr all watching the same
``/mnt/storage/...`` tree) end up replicating the same mapping list in
every integration. The global layer lets them define the mapping once.

Resolution order (see :mod:`app.integrations.path_mapping`):
  1. Per-integration mappings run first (most specific, often
     compensate for that integration's particular view of paths).
  2. Then global mappings (catch-all rules the operator wants applied
     everywhere).

Global mappings are admin-managed via :mod:`app.api.v1.path_mappings`
(GET / POST / PATCH / DELETE under ``/api/v1/system/path-mappings/global``).
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base, TimestampMixin


class GlobalPathMapping(Base, TimestampMixin):
    """A single global ``from_path → to_path`` prefix rewrite.

    Distinct from the per-integration mappings on
    ``Integration.config.path_mappings`` (which live in a JSON blob) —
    global mappings are first-class rows so they can be CRUD'd by
    name, ordered by ``priority``, and individually disabled without
    touching the rest of the list.
    """

    __tablename__ = "global_path_mappings"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    from_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    to_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    # Lower priority applies first. Ties broken by ``created_at`` so
    # ordering is deterministic even when multiple rows share a
    # priority (the typical case — operators rarely tune this knob).
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    # ``created_at`` / ``updated_at`` come from ``TimestampMixin``.
