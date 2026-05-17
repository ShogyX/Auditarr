"""Built-in job runners.

Each runner is a thin async function that takes a session, the bound
arguments, and a runtime context dict. They delegate to the existing
services (scanner, rules, integrations) — the catalogue exists to give
the scheduler and the UI a uniform handle on them.

The ``ctx`` dict carries the worker-scoped singletons (event bus,
registry, ffprobe service). The scheduler populates it from the
WorkerSettings startup hook.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.catalogue import JobCatalogue, JobSpec
from app.core.exceptions import NotFoundError
from app.integrations.manager import IntegrationManager
from app.integrations.tag_sync import IntegrationTagSync
from app.security.secrets import get_secret_box
from app.services.media import Scanner, ScanOptions, get_ffprobe_service
from app.services.repositories import (
    IntegrationRepository,
    LibraryRepository,
)
from app.services.rules_service import RulesService


# ── Library scan ─────────────────────────────────────────────
async def _run_scan_library(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    library_id = args["library_id"]
    library = await LibraryRepository(session).get(library_id)
    if library is None:
        raise NotFoundError(f"Library {library_id!r} not found")
    scanner = Scanner(
        session=session,
        event_bus=ctx["bus"],
        ffprobe=ctx.get("ffprobe") or get_ffprobe_service(),
        registry=ctx.get("registry"),
    )
    report = await scanner.scan(
        library,
        options=ScanOptions(
            mode=args.get("mode", "full"),
            follow_symlinks=bool(args.get("follow_symlinks", False)),
        ),
    )
    return {
        "run_id": report.run_id,
        "status": report.status,
        "files_seen": report.files_seen,
        "files_added": report.files_added,
        "files_updated": report.files_updated,
        "files_orphaned": report.files_orphaned,
        "probe_failures": report.probe_failures,
    }


# ── Integration healthcheck ──────────────────────────────────
async def _run_healthcheck_integration(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    integration_id = args["integration_id"]
    integration = await IntegrationRepository(session).get(integration_id)
    if integration is None:
        raise NotFoundError(f"Integration {integration_id!r} not found")
    manager = IntegrationManager(
        session=session,
        registry=ctx["registry"],
        secret_box=get_secret_box(),
        event_bus=ctx["bus"],
    )
    report = await manager.healthcheck(integration)
    return {
        "integration_id": integration.id,
        "status": report.status,
        "detail": report.detail,
    }


# ── Integration tag sync ─────────────────────────────────────
async def _run_sync_integration_tags(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    integration_id = args["integration_id"]
    integration = await IntegrationRepository(session).get(integration_id)
    if integration is None:
        raise NotFoundError(f"Integration {integration_id!r} not found")
    manager = IntegrationManager(
        session=session,
        registry=ctx["registry"],
        secret_box=get_secret_box(),
        event_bus=ctx["bus"],
    )
    tags = await manager.sync_tags(integration)
    report = await IntegrationTagSync(
        session=session, event_bus=ctx["bus"]
    ).apply(integration, tags)
    return {
        "integration_id": report.integration_id,
        "inserted": report.inserted,
        "removed": report.removed,
        "title_count": report.title_count,
    }


# ── Rule evaluation ──────────────────────────────────────────
async def _run_evaluate_library(
    session: AsyncSession, args: dict[str, Any], ctx: dict[str, Any]
) -> dict[str, Any]:
    library_id = args["library_id"]
    # Stage 18 (audit follow-up): optional ``tags`` scope. Empty list
    # and missing key both mean "every file in the library".
    tags_any = args.get("tags") or None
    service = RulesService(
        session=session,
        event_bus=ctx["bus"],
        registry=ctx.get("registry"),
    )
    count = await service.evaluate_library(library_id, tags_any=tags_any)
    return {
        "library_id": library_id,
        "files_evaluated": count,
        "tags_any": tags_any,
    }


# ── Stage 10 (v1.7) — VirusTotal queue drain ──────────────────
async def _run_drain_vt_queue(
    session: AsyncSession,
    args: dict[str, Any],
    ctx: dict[str, Any],
) -> dict[str, Any]:
    """Drain :data:`vt_queue` against the configured VT
    integration.

    Looks up the enabled VirusTotal integration row, decrypts
    its API key, and delegates the per-row work to
    :func:`plugins.virustotal.backend.drain_vt_queue`.

    No-op (returns ``{"reason": "no_vt_integration"}``) when
    no enabled VT integration is configured — the operator
    can schedule this job pre-emptively without it failing
    loudly.
    """
    from sqlalchemy import select

    from app.models.integration import Integration
    from plugins.virustotal.backend import (
        VT_DAILY_CEILING_DEFAULT,
        VT_DRAIN_DEFAULT_BATCH_SIZE,
        VT_MONTHLY_CEILING_DEFAULT,
        drain_vt_queue,
    )

    integration = (
        await session.execute(
            select(Integration)
            .where(Integration.kind == "virustotal")
            .where(Integration.enabled.is_(True))
            .order_by(Integration.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if integration is None:
        return {"reason": "no_vt_integration", "examined": 0}

    manager = IntegrationManager(
        session=session,
        registry=ctx["registry"],
        secret_box=get_secret_box(),
        event_bus=ctx["bus"],
    )
    config = manager.build_config(integration)
    api_key = str(config.secrets.get("api_key", "")).strip()
    if not api_key:
        return {"reason": "missing_api_key", "examined": 0}

    options = config.options or {}
    daily_quota = int(
        options.get("daily_quota") or VT_DAILY_CEILING_DEFAULT
    )
    monthly_quota = int(
        options.get("monthly_quota") or VT_MONTHLY_CEILING_DEFAULT
    )
    timeout = float(options.get("timeout_seconds") or 10)
    batch_size = int(
        args.get("batch_size") or VT_DRAIN_DEFAULT_BATCH_SIZE
    )

    counters = await drain_vt_queue(
        session,
        integration_id=integration.id,
        api_key=api_key,
        daily_quota=daily_quota,
        monthly_quota=monthly_quota,
        timeout=timeout,
        batch_size=batch_size,
        event_bus=ctx["bus"],
    )
    return counters


# ── Registration ─────────────────────────────────────────────
def register_builtin_jobs(catalogue: JobCatalogue) -> None:
    """Populate the catalogue with the jobs that ship in-box."""
    catalogue.register(
        JobSpec(
            key="scan_library",
            label="Scan library",
            description="Walk a library's filesystem, classify files, and run ffprobe.",
            args_schema={
                "type": "object",
                "required": ["library_id"],
                "properties": {
                    "library_id": {
                        "type": "string",
                        "title": "Library",
                        # Stage 17 (audit follow-up): tells the frontend
                        # ArgInput to render a Select populated from
                        # the existing /libraries call rather than a
                        # free-text input that demands a UUID.
                        "format": "library_id",
                    },
                    "mode": {
                        "type": "string",
                        "title": "Scan mode",
                        "default": "full",
                        "enum": ["full", "incremental", "targeted", "rescan"],
                    },
                    "follow_symlinks": {
                        "type": "boolean",
                        "title": "Follow symlinks",
                        "default": False,
                    },
                },
            },
            required_args=("library_id",),
            timeout_seconds=60 * 60,
            runner=_run_scan_library,
        )
    )
    catalogue.register(
        JobSpec(
            key="healthcheck_integration",
            label="Healthcheck integration",
            description="Verify reachability of one integration and persist the result.",
            args_schema={
                "type": "object",
                "required": ["integration_id"],
                "properties": {
                    "integration_id": {
                        "type": "string",
                        "title": "Integration",
                        # Stage 17 (audit follow-up): Select populated
                        # from /integrations rather than a free-text
                        # UUID input.
                        "format": "integration_id",
                    }
                },
            },
            required_args=("integration_id",),
            timeout_seconds=60,
            runner=_run_healthcheck_integration,
        )
    )
    catalogue.register(
        JobSpec(
            key="sync_integration_tags",
            label="Sync integration tags",
            description="Pull tags from an integration and reconcile media_tags.",
            args_schema={
                "type": "object",
                "required": ["integration_id"],
                "properties": {
                    "integration_id": {
                        "type": "string",
                        "title": "Integration",
                        "format": "integration_id",
                    }
                },
            },
            required_args=("integration_id",),
            timeout_seconds=300,
            runner=_run_sync_integration_tags,
        )
    )
    catalogue.register(
        JobSpec(
            key="evaluate_library",
            label="Evaluate rules for library",
            description="Re-run every enabled rule against every file in a library.",
            args_schema={
                "type": "object",
                "required": ["library_id"],
                "properties": {
                    "library_id": {
                        "type": "string",
                        "title": "Library",
                        "format": "library_id",
                    },
                    # Stage 18 (audit follow-up): tag scope. Optional.
                    # When set, only files carrying at least one of
                    # the listed tags are re-evaluated.
                    "tags": {
                        "type": "array",
                        "title": "Restrict to tags (optional)",
                        "items": {"type": "string"},
                        "format": "tag_list",
                        "default": [],
                    },
                },
            },
            required_args=("library_id",),
            timeout_seconds=600,
            runner=_run_evaluate_library,
        )
    )

    # ── Stage 10 (v1.7) — VirusTotal queue drain ─────────────────
    catalogue.register(
        JobSpec(
            key="drain_vt_queue",
            label="Drain VirusTotal queue",
            description=(
                "Pull up to N entries from vt_queue, look each up "
                "against VirusTotal, and persist the result onto "
                "the matched MediaFile. Honours the 3-window quota "
                "(per-minute / per-day / per-month)."
            ),
            args_schema={
                "type": "object",
                "properties": {
                    "batch_size": {
                        "type": "integer",
                        "title": "Batch size",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 200,
                        "description": (
                            "How many queue entries to process per "
                            "tick. The free-tier per-minute ceiling "
                            "is 4 — 20 batches comfortably inside a "
                            "5-minute schedule."
                        ),
                    },
                },
            },
            required_args=(),
            timeout_seconds=300,
            runner=_run_drain_vt_queue,
        )
    )
