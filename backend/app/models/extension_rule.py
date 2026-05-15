"""Media extension rules (Stage 9 audit follow-up).

Per-extension disposition. Pre-Stage-9 the scanner walked everything
under a library and applied a single classification heuristic
(``app.services.media.classify``). Operators reported wanting more
fine-grained control:

  - ``ignore``        — skip during scan; never indexed
  - ``stats_only``    — indexed but never notified / never optimized
  - ``malicious``     — severity=crit + quarantined on scan
  - ``accepted``      — severity capped at ok (never escalates)

The disposition is read by the scanner at scan-start so a one-off
flip survives across libraries without restart.

The table is intentionally small + deliberately a separate model
(not buried in JSON in runtime_settings) so:

  - The shape is first-class (each row CRUD-able by extension).
  - Operators can find + edit per-row via the API + a future UI.
  - The scanner can index by ``extension`` for O(log n) lookup.

Admin-managed via :mod:`app.api.v1.extension_rules`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base, TimestampMixin


class MediaExtensionRule(Base, TimestampMixin):
    """A per-extension rule that overrides the scanner's default
    classification + the rule-engine's severity policy."""

    __tablename__ = "media_extension_rules"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    # Lower-cased, no leading dot. Unique per row so an operator
    # can't accidentally create two conflicting policies for the
    # same extension.
    extension: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True
    )
    # One of the four dispositions documented in this module's
    # docstring. Stored as a String (not Enum) so adding a fifth
    # disposition is a code change in one place, not a schema
    # migration.
    disposition: Mapped[str] = mapped_column(
        String(32), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
