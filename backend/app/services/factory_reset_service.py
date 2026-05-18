"""Factory-reset service (v1.9 Stage 2.6).

Wipes Auditarr back to a fresh-install state while keeping the
operator's login credentials intact, so the operator can keep
working from the same browser session without having to redo the
install flow.

What's preserved:
  * ``users`` — so the admin can log back in. Sessions are NOT
    preserved; the operator's current token continues to work for
    the rest of the request, but any other open browser tab will
    need to log in again.
  * ``audit_log`` — the factory-reset event ITSELF lives here, so
    truncating it would erase the proof of what we did. We keep
    the whole log so prior security-relevant audit history isn't
    silently destroyed.
  * ``alembic_version`` — the schema-migration bookkeeping table.
    Truncating it would leave the DB at a known schema but with
    no record of which migration produced it, which makes the
    next ``alembic upgrade head`` re-run every migration. Keep
    it; the schema doesn't change here.

What's wiped:
  * Every other application table — media, libraries, rules,
    integrations, optimization queue, playback events, etc.
  * ``data_dir/trash/`` — everything trash-bucketed by either the
    rule engine or the v1.9 Stage 2.4 operator-delete service.
  * Runtime setting overrides + change history. The defaults
    defined in code take over after the reset.

What's recorded:
  * One ``AuditLogEntry`` with ``action="factory_reset"``,
    ``actor_id`` set to the operator who triggered it, the
    confirm-phrase they typed in metadata, and a count of how
    many tables were truncated. The entry persists across the
    reset because audit_log is on the preserved list.

The endpoint is admin-only, requires a typed confirmation phrase,
and runs in a single transaction so a mid-reset failure rolls back
the entire wipe.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import text

from app.core.logging import get_logger
from app.services.audit_service import AuditService
from app.storage.base import Base

if TYPE_CHECKING:
    from app.core.settings import Settings
    from sqlalchemy.ext.asyncio import AsyncSession

log = get_logger("auditarr.system.factory_reset", category="system")

CONFIRM_PHRASE: str = "reset auditarr"
"""The exact string the operator must supply. Lowercase so the UI
can render it as a code block without case-sensitivity worries."""

PRESERVED_TABLES: frozenset[str] = frozenset(
    {
        "users",
        "audit_log",
        "alembic_version",
    }
)
"""Tables that survive a factory reset. See module docstring for
rationale; if you add a table here, also document why."""


@dataclass(slots=True)
class FactoryResetResult:
    """Returned to the API endpoint and surfaced in the audit log."""

    tables_truncated: int
    """How many application tables were wiped. The preserved list
    is excluded from this count."""

    trash_purged: bool
    """True iff the trash dir existed AND was successfully cleared.
    False if the dir didn't exist (no-op success) or if the rmtree
    call hit an error (the reset continues; the operator sees a
    warning)."""


class FactoryResetService:
    """Drives the wipe + the audit-log record of having done it."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
    ) -> None:
        self._session = session
        self._settings = settings
        self._audit = AuditService(session)

    async def reset(
        self,
        *,
        actor_id: str | None,
        confirm_phrase: str,
    ) -> FactoryResetResult:
        """Run the full reset. Raises ``ValueError`` if the
        confirm-phrase is wrong — the router translates that to a
        422 so the operator sees a typed-confirmation error instead
        of a generic 500."""
        if confirm_phrase.strip().lower() != CONFIRM_PHRASE:
            raise ValueError(
                f"confirm_phrase must be exactly {CONFIRM_PHRASE!r}"
            )

        # ── 1. Truncate every non-preserved table.
        # We iterate ``Base.metadata.sorted_tables`` in REVERSE so
        # child tables come before parents — this lets us issue
        # plain DELETE statements without violating foreign-key
        # constraints on sqlite (which doesn't support
        # TRUNCATE … CASCADE). Postgres would handle either form;
        # the DELETE path works on both.
        truncated_count = 0
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in PRESERVED_TABLES:
                continue
            await self._session.execute(text(f'DELETE FROM "{table.name}"'))
            truncated_count += 1

        # ── 2. Purge the trash directory if it exists.
        trash_dir = Path(self._settings.data_dir) / "trash"
        trash_purged = False
        if trash_dir.exists():
            try:
                shutil.rmtree(trash_dir)
                # Recreate empty so the next operator-delete doesn't
                # have to mkdir its bucket-root from scratch.
                trash_dir.mkdir(parents=True, exist_ok=True)
                trash_purged = True
                log.info(
                    "system.factory_reset.trash_purged",
                    path=str(trash_dir),
                )
            except OSError as exc:
                # An rmtree failure shouldn't abort the wipe —
                # the database side already happened and the audit
                # row will say so.
                log.error(
                    "system.factory_reset.trash_purge_failed",
                    path=str(trash_dir),
                    error=str(exc),
                )

        # ── 3. Write the audit-log entry. This row WILL survive
        # subsequent normal use because the audit_log table is
        # preserved. The operator can later filter the log for
        # ``action="factory_reset"`` to see every reset that
        # happened on this install.
        await self._audit.record(
            action="factory_reset",
            actor_id=actor_id,
            actor_label="operator",
            target_type="system",
            target_id=None,
            metadata={
                "confirm_phrase": confirm_phrase,
                "tables_truncated": truncated_count,
                "trash_purged": trash_purged,
                "preserved_tables": sorted(PRESERVED_TABLES),
            },
        )

        return FactoryResetResult(
            tables_truncated=truncated_count,
            trash_purged=trash_purged,
        )


__all__ = [
    "FactoryResetService",
    "FactoryResetResult",
    "CONFIRM_PHRASE",
    "PRESERVED_TABLES",
]
