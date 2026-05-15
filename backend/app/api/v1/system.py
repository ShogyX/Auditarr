"""System / app metadata endpoints."""

from __future__ import annotations

import platform
import sys
from datetime import UTC
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from app import __version__
from app.api.auth_deps import AdminUser, CurrentUser
from app.api.dependencies import EventBusDep, SessionDep, SettingsDep
from app.api.websocket import get_ws_manager

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/info", summary="Application metadata")
async def info(
    _user: CurrentUser, settings: SettingsDep, _bus: EventBusDep
) -> dict[str, Any]:
    # Bug-hunt 3: previously open to unauthenticated callers.
    # Leaked ``platform.platform()`` (host OS + kernel version)
    # and ``sys.version`` — low-severity recon info that helps
    # an attacker scope CVEs against the running host. The
    # ``/version`` endpoint below stays open because it's used
    # by the login-screen sidebar poll and only returns the
    # app version (no host detail). ``CurrentUser`` not
    # ``AdminUser`` because viewers operating curl probes
    # against their own deployment should still get the data.
    return {
        "name": "auditarr",
        "version": __version__,
        # Stage 11: the image-stamped version. Distinct from
        # ``__version__`` which is the in-source SDK version and only
        # bumps on schema-breaking releases.
        "app_version": settings.app_version,
        "env": settings.env,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "api_root": settings.api_root,
        "websocket_clients": get_ws_manager().connection_count,
    }


@router.get(
    "/version",
    summary="Lightweight version probe for the updater UI",
)
async def version(settings: SettingsDep) -> dict[str, Any]:
    """Cheap endpoint the dashboard sidebar can hit every minute.

    Distinct from ``/info`` so the sidebar doesn't pull the full app
    metadata block on every poll.
    """
    return {
        "app_version": settings.app_version,
        "sdk_version": __version__,
    }


# ── Stage 20: read-only config surface for the Settings UI ───
# Operators want to see "what's my current config?" without SSH-ing
# in to read the env file. We surface a structured view organized
# into sections the UI can render. Secrets and the database URL are
# never returned — only metadata that's safe to display.
#
# All fields are read-only here. Editing requires changing the env
# file (``/etc/auditarr/auditarr.env`` for bare-metal, ``.env`` for
# Docker) and restarting the service. The UI displays this fact
# next to each section.
def _redact_url(url: str) -> str:
    """Strip the password from a connection URL, leaving everything
    else visible so an operator can sanity-check the host/db."""
    import re

    return re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", url)


@router.get(
    "/config",
    summary="Operator-facing config view (read-only)",
)
async def config(_admin: AdminUser, settings: SettingsDep) -> dict[str, Any]:
    """Return the current operational config grouped by section.

    Used by the Settings page to display the live env-driven config
    without exposing secrets. Admin-only because it leaks deployment
    topology (file paths, URLs minus passwords, etc.).
    """
    return {
        "api": {
            "host": settings.host,
            "port": settings.port,
            "api_prefix": settings.api_prefix,
            "api_version": settings.api_version,
            "allowed_origins": list(settings.allowed_origins),
            "ws_require_auth": settings.ws_require_auth,
            "log_level": settings.log_level,
            "log_format": settings.log_format,
            "env": settings.env,
        },
        "auth": {
            "access_token_ttl_minutes": settings.access_token_ttl_minutes,
            "refresh_token_ttl_days": settings.refresh_token_ttl_days,
            "rate_limit_attempts": settings.auth_rate_limit_attempts,
            "rate_limit_window_seconds": settings.auth_rate_limit_window_seconds,
        },
        "storage": {
            "database_url": _redact_url(settings.database_url),
            "database_pool_size": settings.database_pool_size,
            "database_max_overflow": settings.database_max_overflow,
            "redis_url": _redact_url(settings.redis_url),
            "queue_name": settings.queue_name,
            "data_dir": str(settings.data_dir),
            "plugin_dir": str(settings.plugin_dir),
            "builtin_plugin_dir": str(settings.builtin_plugin_dir),
            "docs_dir": str(settings.docs_dir),
            "frontend_dist": (
                str(settings.frontend_dist) if settings.frontend_dist else None
            ),
        },
        "updater": {
            "feed_url": settings.update_feed_url,
            "check_interval_minutes": settings.update_check_interval_minutes,
            "install_mode": settings.update_install_mode,
            "apply_sentinel": str(settings.update_apply_sentinel),
            "apply_status_path": str(settings.update_apply_status_path),
        },
        "plugins": {
            "gallery_url": settings.plugin_gallery_url,
        },
        "housekeeping": {
            "delivery_retention_days": settings.housekeeping_delivery_retention_days,
            "update_check_retention_days": settings.housekeeping_update_check_retention_days,
            "rule_evaluation_retention_days": settings.housekeeping_rule_evaluation_retention_days,
            "job_run_retention_days": settings.housekeeping_job_run_retention_days,
        },
    }


@router.get("/capabilities", summary="Registered service capabilities")
async def capabilities() -> dict[str, list[str]]:
    from app.core.registry import get_registry

    registry = get_registry()
    return {cap: [type(p).__name__ for p in registry.providers_for(cap)]
            for cap in registry.capabilities()}


# ── Consolidated audit follow-up: changelog endpoint ─────────
# Stage 12 of the audit shipped a Changelog page on the frontend that
# expected this endpoint. The page handles a 404 gracefully (renders a
# friendly empty state), so this endpoint can land independently of
# any frontend redeploy. The CHANGELOG.md file lives at the project
# root — we resolve it relative to ``settings.data_dir.parent`` for
# the bare-metal install layout, with a fallback to a path walk for
# Docker / development setups where the file is alongside the
# ``backend/`` directory.
def _find_changelog() -> Path | None:
    """Locate the project-root ``CHANGELOG.md``.

    (Stage 1 / L2) Two bugs to address:

    1. The previous import was ``from app.config import get_settings``,
       which does not exist — the settings module lives at
       ``app.core.settings``. The ``except Exception`` swallowed the
       ImportError silently, so the bare-metal candidate was never
       contributed to the search list.
    2. The dev-layout walk stopped at the first directory containing a
       ``pyproject.toml``, but the project layout has
       ``backend/pyproject.toml`` while ``CHANGELOG.md`` lives at the
       repository root one level higher. The walk therefore stopped at
       the ``backend/`` directory and never saw the CHANGELOG. The fix
       is to additionally check ``parent.parent`` (one level above the
       pyproject root) before returning ``None``.
    """
    candidates: list[Path] = []

    # Bare-metal layout: /opt/auditarr/CHANGELOG.md (settings.data_dir
    # is /opt/auditarr/data, so .parent is /opt/auditarr).
    try:
        from app.core.settings import get_settings

        s = get_settings()
        candidates.append(s.data_dir.parent / "CHANGELOG.md")
    except Exception:
        # Settings import or instantiation may legitimately fail in
        # tooling contexts (e.g. alembic migrations running with a
        # minimal env). Fall through to the layout-walk below.
        pass

    # Dev layout: walk up from this file looking for CHANGELOG.md.
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        candidates.append(parent / "CHANGELOG.md")
        if (parent / "pyproject.toml").exists():
            # ``pyproject.toml`` lives under ``backend/``, but the
            # CHANGELOG lives one level up at the repository root.
            # Probe that location explicitly before stopping the walk.
            candidates.append(parent.parent / "CHANGELOG.md")
            break

    for c in candidates:
        if c.is_file():
            return c
    return None


@router.get(
    "/changelog",
    summary="Project CHANGELOG.md content (HTML + raw markdown)",
)
async def changelog(_user: CurrentUser) -> dict[str, Any]:
    """Return CHANGELOG.md as rendered HTML plus raw markdown.

    Surfaces the version history file the project ships at its
    root. Returns 404 if the file isn't present at any known
    location — the frontend handles that case with a friendly
    empty-state, so unavailability is not a hard error.
    """
    from datetime import datetime

    from fastapi import HTTPException
    from markdown_it import MarkdownIt

    path = _find_changelog()
    if path is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "CHANGELOG.md not found at the expected project-root "
                "location. Check the install layout."
            ),
        )

    body_md = path.read_text(encoding="utf-8")
    # Same renderer the docs system uses (see backend/app/documentation/loader.py).
    md = MarkdownIt(
        "commonmark", {"html": False, "linkify": True, "typographer": True}
    )
    body_html = md.render(body_md)

    try:
        stat = path.stat()
        last_modified: str | None = datetime.fromtimestamp(
            stat.st_mtime, tz=UTC
        ).isoformat()
    except OSError:
        last_modified = None

    return {
        "body_html": body_html,
        "body_markdown": body_md,
        "last_modified": last_modified,
    }


# ── Housekeeping (Stage 14 audit follow-up) ───────────────────
@router.post(
    "/housekeeping/run",
    summary="Run housekeeping immediately (Stage 14)",
)
async def run_housekeeping(
    _admin: AdminUser,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Trim audit-style tables right now. Returns the same shape as
    the scheduled tick's report plus a ``trigger: "manual"`` field
    so the caller can confirm which path executed.

    Runs synchronously on the API process per the Stage 14 guard
    rail — the point of surfacing the button is "delete it now".
    """
    from app.housekeeping import HousekeepingService

    service = HousekeepingService(session=session, settings=settings)
    report = await service.run(trigger="manual")
    return {
        "trigger": "manual",
        "notification_deliveries": report.notification_deliveries,
        "update_checks": report.update_checks,
        "rule_evaluations": report.rule_evaluations,
        "job_runs": report.job_runs,
        "total": report.total,
    }


@router.get(
    "/housekeeping/last-run",
    summary="Last housekeeping run report (Stage 14)",
)
async def last_housekeeping_run(
    _admin: AdminUser,
    session: SessionDep,
) -> dict[str, Any] | None:
    """Return the most recent ``housekeeping_runs`` row, or ``null``
    if the system has never run. The Settings page renders the row
    inline; null surfaces a "Never run yet" line."""
    from sqlalchemy import select

    from app.models.housekeeping_run import HousekeepingRun

    row = (
        await session.execute(
            select(HousekeepingRun)
            .order_by(HousekeepingRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "id": row.id,
        "trigger": row.trigger,
        "started_at": row.started_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "deliveries_deleted": row.deliveries_deleted,
        "update_checks_deleted": row.update_checks_deleted,
        "rule_evaluations_deleted": row.rule_evaluations_deleted,
        "job_runs_deleted": row.job_runs_deleted,
        "error": row.error,
    }
