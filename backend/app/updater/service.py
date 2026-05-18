"""Updater service.

Top-level orchestrator for the updater. The five entry points:

* :meth:`check_now` — hit the feed, persist a check row, emit
  ``update.available`` if the result is newer than what's installed.
* :meth:`get_status` — return the combined view (installed, latest,
  whether an update is available, last check, recent checks/applies).
  Used by the status endpoint and the sidebar badge.
* :meth:`request_apply` — write the sentinel file the host helper
  watches, plus persist an UpdateApply row. Does NOT do the actual
  update.
* :meth:`poll_apply_status` — read the status file the host helper
  writes, transition any open UpdateApply rows.
* :meth:`rollback` — mark a completed apply as rolled-back. The actual
  re-deploy of the previous image is the host helper's job (same
  sentinel mechanism, different target version).

The sentinel/status file protocol is intentionally simple plain text
so the host script can stay short and avoid Python deps. See
``docker/updater/auditarr-update.sh`` (shipped in Stage 11 turn 2)
for the consumer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.settings import Settings
from app.events.bus import EventBus
from app.models.update_apply import UpdateApply
from app.models.update_check import UpdateCheck
from app.services.repositories.updater import (
    UpdateApplyRepository,
    UpdateCheckRepository,
)
from app.updater.feed import FeedResult, fetch_feed
from app.updater.install_mode import detect_install_mode
from app.updater.versioning import is_newer
from app.utils.datetime import utcnow

log = get_logger("auditarr.updater.service", category="updater")


@dataclass(slots=True)
class UpdaterStatus:
    """What the status endpoint returns to the UI."""

    installed_version: str
    latest_version: str | None
    has_update: bool
    last_checked_at: str | None
    last_check_ok: bool | None
    last_check_detail: str | None
    feed_url: str
    apply_in_progress: bool
    # Stage 19: which install environment we're in. The UI uses this
    # to show appropriate "Updating <docker|systemd>…" copy and to
    # disable the Apply button when no helper is wired up.
    install_mode: str
    # When install_mode == "unmanaged", apply is disabled regardless
    # of feed result. Surfacing this as a separate flag (rather than
    # making the UI derive it) keeps the contract explicit.
    apply_enabled: bool


class UpdaterService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        event_bus: EventBus | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._bus = event_bus
        self._checks = UpdateCheckRepository(session)
        self._applies = UpdateApplyRepository(session)

    # ── Check ───────────────────────────────────────────────────
    async def check_now(self) -> UpdateCheck:
        """Hit the feed, persist a row, emit event if newer."""
        feed_result = await fetch_feed(self._settings.update_feed_url)
        now = utcnow()
        row = UpdateCheck(
            checked_at=now,
            ok=feed_result.ok,
            latest_version=feed_result.version,
            changelog=feed_result.changelog,
            detail=feed_result.detail,
            feed_url=self._settings.update_feed_url,
        )
        await self._checks.add(row)
        await self._session.commit()

        if (
            feed_result.ok
            and feed_result.version
            and is_newer(feed_result.version, self._settings.app_version)
            and self._bus is not None
        ):
            await self._bus.emit(
                "update.available",
                {
                    "installed": self._settings.app_version,
                    "latest": feed_result.version,
                },
                source="updater",
            )
        log.info(
            "updater.check_complete",
            ok=feed_result.ok,
            latest=feed_result.version,
            installed=self._settings.app_version,
        )
        return row

    # ── Status ──────────────────────────────────────────────────
    async def get_status(self) -> UpdaterStatus:
        last = await self._checks.latest()
        latest_version = last.latest_version if last and last.ok else None
        has_update = bool(
            latest_version
            and is_newer(latest_version, self._settings.app_version)
        )
        # An apply is "in progress" if there's a row in requested/running
        # state. We don't lock; the host script picks the most recent
        # requested row and the status file resolves any ambiguity.
        # v1.9 Stage 1.2 — pass the apply-timeout so ``has_open`` first
        # reaps any rows older than the cutoff. This is the authoritative
        # reaper: it runs every time the status endpoint is hit
        # (every 30 s from the UI's poll), so a wedged apply is auto-
        # cleared within the timeout window with no operator action.
        in_progress = await self._applies.has_open(
            timeout_seconds=self._settings.update_apply_timeout_seconds,
        )
        install_mode = detect_install_mode(self._settings.update_install_mode)
        return UpdaterStatus(
            installed_version=self._settings.app_version,
            latest_version=latest_version,
            has_update=has_update,
            last_checked_at=last.checked_at.isoformat() if last else None,
            last_check_ok=last.ok if last else None,
            last_check_detail=last.detail if last else None,
            feed_url=self._settings.update_feed_url,
            apply_in_progress=in_progress,
            install_mode=install_mode,
            apply_enabled=install_mode != "unmanaged",
        )

    # ── Apply ───────────────────────────────────────────────────
    async def request_apply(
        self, *, to_version: str, triggered_by_user_id: str | None
    ) -> UpdateApply:
        """Write the sentinel file and persist an UpdateApply row.

        Raises :class:`ValueError` if:
        * Another apply is already open — we don't want two host
          scripts racing on the same compose project.
        * The install mode is ``unmanaged`` — there's no helper script
          to consume the sentinel so an apply would just sit forever.
          The UI should already have grayed out the Apply button via
          ``apply_enabled=False`` on the status endpoint, but we
          enforce it server-side too.
        """
        install_mode = detect_install_mode(self._settings.update_install_mode)
        if install_mode == "unmanaged":
            raise ValueError(
                "Update apply is disabled: install environment is "
                "'unmanaged'. Configure AUDITARR_UPDATE_INSTALL_MODE "
                "or update Auditarr by hand."
            )
        if await self._applies.has_open(
            timeout_seconds=self._settings.update_apply_timeout_seconds,
        ):
            raise ValueError(
                "Another update apply is already in progress"
            )

        now = utcnow()
        row = UpdateApply(
            status="requested",
            from_version=self._settings.app_version,
            to_version=to_version,
            started_at=now,
            triggered_by_user_id=triggered_by_user_id,
            detail=None,
            error=None,
        )
        await self._applies.add(row)
        await self._session.commit()

        # Write the sentinel after the DB row so the host helper can
        # always cross-reference its work with an audit row.
        sentinel: Path = self._settings.update_apply_sentinel
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text(
            json.dumps(
                {
                    "apply_id": row.id,
                    "from_version": self._settings.app_version,
                    "to_version": to_version,
                    "requested_at": now.isoformat(),
                }
            ),
            encoding="utf-8",
        )
        log.info(
            "updater.apply_requested",
            apply_id=row.id,
            to_version=to_version,
            sentinel=str(sentinel),
        )
        return row

    async def poll_apply_status(self) -> UpdateApply | None:
        """Reconcile open UpdateApply rows with the host helper's status.

        Returns the row that was transitioned, if any. The host helper
        writes a JSON status file shaped like::

            {"apply_id": "...", "status": "completed", "detail": "..."}

        We delete the status file after consuming it so subsequent ticks
        don't re-fire events for the same outcome.
        """
        status_path: Path = self._settings.update_apply_status_path
        if not status_path.exists():
            return None
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning(
                "updater.status_file_unreadable",
                path=str(status_path),
                error=str(exc),
            )
            return None

        apply_id = payload.get("apply_id")
        new_status = payload.get("status")
        if not isinstance(apply_id, str) or new_status not in {
            "running",
            "completed",
            "failed",
        }:
            log.warning(
                "updater.status_file_invalid", payload=payload
            )
            return None

        row = await self._applies.get(apply_id)
        if row is None:
            log.warning("updater.status_file_unknown_apply", apply_id=apply_id)
            try:
                status_path.unlink()
            except OSError:
                pass
            return None

        row.status = new_status
        if new_status in {"completed", "failed"}:
            row.finished_at = utcnow()
        if isinstance(payload.get("detail"), str):
            row.detail = payload["detail"]
        if isinstance(payload.get("error"), str):
            row.error = payload["error"]
        await self._session.commit()

        if self._bus is not None:
            if new_status == "completed":
                await self._bus.emit(
                    "update.installed",
                    {"apply_id": row.id, "to_version": row.to_version},
                    source="updater",
                )
            elif new_status == "failed":
                await self._bus.emit(
                    "update.failed",
                    {
                        "apply_id": row.id,
                        "to_version": row.to_version,
                        "error": row.error,
                    },
                    source="updater",
                )

        # Done with the status file; clean up. If unlink fails (e.g.
        # read-only mount), the next tick will read the same payload and
        # the status transition guard above (only requested → known) will
        # noop safely.
        try:
            status_path.unlink()
        except OSError:
            pass
        return row

    # ── Force-clear (v1.9 Stage 1.2) ────────────────────────────
    async def force_clear(self, apply_id: str) -> UpdateApply:
        """Operator escape hatch: flip a stuck row to ``failed``.

        Authoritative reaping happens in :meth:`get_status` /
        :meth:`request_apply` (via the timeout-aware ``has_open``),
        but operators sometimes want to clear a wedge immediately
        rather than wait for the timeout. This is the manual lever.

        Raises ``ValueError`` if the row is unknown or not in an open
        state — surfaced as 404 / 422 by the router.
        """
        row = await self._applies.force_clear(apply_id)
        await self._session.commit()
        if self._bus is not None:
            await self._bus.emit(
                "update.failed",
                {
                    "apply_id": row.id,
                    "to_version": row.to_version,
                    "error": row.error,
                },
                source="updater",
            )
        log.info(
            "updater.apply_force_cleared",
            apply_id=row.id,
            to_version=row.to_version,
        )
        return row

    # ── Rollback ────────────────────────────────────────────────
    async def rollback(self, apply_id: str) -> UpdateApply:
        """Re-request the previous version as a fresh apply.

        Stage 11 keeps this simple: we mark the old row as
        ``rolled_back`` and create a new ``requested`` row targeting the
        old ``from_version``. The host helper handles it the same as any
        other apply — it's the operator's responsibility to make sure
        the previous image tag still exists on the registry.
        """
        row = await self._applies.get(apply_id)
        if row is None:
            raise ValueError(f"Unknown apply {apply_id!r}")
        if row.from_version is None:
            raise ValueError(
                "Cannot roll back an apply with no recorded from_version"
            )
        if row.status != "completed":
            raise ValueError(
                f"Cannot roll back apply in status {row.status!r}"
            )
        row.status = "rolled_back"
        row.finished_at = utcnow()
        await self._session.commit()
        # Now request the previous version via the normal apply flow.
        return await self.request_apply(
            to_version=row.from_version,
            triggered_by_user_id=row.triggered_by_user_id,
        )


__all__ = ["UpdaterService", "UpdaterStatus", "FeedResult"]
