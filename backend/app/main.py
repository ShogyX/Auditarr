"""FastAPI application factory.

The lifespan brings up infrastructure in order — settings → logging → DB →
Redis → event bus → websocket bridge → plugin discovery — and tears it
down in reverse on shutdown.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.errors import install_error_handlers
from app.api.middleware import install_middleware
from app.api.v1 import api_v1_router
from app.api.v1.ws import router as ws_router
from app.api.websocket import get_ws_manager
from app.core.logging import configure_logging, get_logger
from app.core.registry import get_registry
from app.core.settings import Settings, get_settings
from app.events.bus import get_event_bus
from app.plugins.loader import get_plugin_loader
from app.storage.cache import get_redis
from app.storage.database import get_database


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    log = get_logger("auditarr.startup", category="system")

    # ── Storage ────────────────────────────────────────────
    db = get_database()
    redis = get_redis()
    await db.connect()
    await redis.connect()

    # ── Admin bootstrap (first-boot only) ──────────────────
    from app.security.bootstrap import bootstrap_admin_if_needed

    await bootstrap_admin_if_needed(db)

    # ── Core services into the registry ────────────────────
    registry = get_registry()
    bus = get_event_bus()
    registry.register(Settings, settings, replace=True)
    registry.register(type(db), db, replace=True)
    registry.register(type(redis), redis, replace=True)
    registry.register(type(bus), bus, replace=True)

    # ── Realtime bridge ────────────────────────────────────
    ws_manager = get_ws_manager()
    await ws_manager.start()

    # ── Plugins ────────────────────────────────────────────
    loader = get_plugin_loader()
    await loader.discover_and_load(app, route_prefix=settings.api_root)

    # ── Documentation index ────────────────────────────────
    from app.documentation import get_documentation_service

    docs_service = get_documentation_service()
    docs_service.load()
    registry.register(type(docs_service), docs_service, replace=True)

    # Stage 12: fire each loaded plugin's ``on_startup`` *after* every
    # other host service is up. Plugins typically need DB / event bus /
    # docs available, so this ordering avoids surprising "service not
    # ready" errors inside long-running background tasks.
    await loader.start()

    # ── Stage 21: runtime settings ─────────────────────────
    # Apply persisted overrides to the in-process Settings before we
    # announce ``system.startup`` — listeners may read overridden
    # values during their startup paths.
    from app.services.runtime_settings import (
        load_and_apply_overrides,
        reload_listener,
    )

    async with db.session() as session:
        await load_and_apply_overrides(session, settings)

    # ── Stage 29: seed builtin rules ───────────────────────
    # Idempotent. Inserts new builtins, refreshes the
    # codebase-owned fields (description + definition) on existing
    # ones, leaves operator-controlled fields (enabled, priority,
    # last_*) alone. Runs after settings are applied so any
    # operator override that disables seeding (future hook —
    # nothing today) would be respected; runs before plugins'
    # on_startup so plugin code can rely on builtins being
    # present. Failures here are logged but don't abort startup —
    # a missing builtin shouldn't keep the app from booting.
    from app.rules.builtin import register_builtin_rules

    try:
        async with db.session() as session:
            stats = await register_builtin_rules(session)
        log.info("builtin_rules.seeded", **stats)
    except Exception as exc:  # noqa: BLE001
        log.warning("builtin_rules.seed_failed", error=str(exc))

    # Background task: re-apply overrides whenever any process
    # publishes a reload notification. Stored on the app state so we
    # can cancel it cleanly on shutdown.
    app.state.settings_reload_task = asyncio.create_task(
        reload_listener(settings),
        name="settings-reload-listener",
    )

    await bus.emit("system.startup", {"version": __version__}, source="core")
    log.info("system.started", version=__version__, env=settings.env)

    try:
        yield
    finally:
        log.info("system.shutting_down")
        await bus.emit("system.shutdown", {"version": __version__}, source="core")
        # Cancel the reload listener before tearing down Redis — once
        # the client is gone the listener's pubsub iterator raises and
        # spams the log.
        reload_task = getattr(app.state, "settings_reload_task", None)
        if reload_task is not None:
            reload_task.cancel()
            try:
                await reload_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await loader.shutdown()
        await ws_manager.stop()
        await redis.disconnect()
        await db.disconnect()
        registry.clear()
        from app.documentation import reset_documentation_service

        reset_documentation_service()


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="Auditarr",
        version=__version__,
        description="Self-hosted media library auditor.",
        # ``/api/v1/docs`` is reserved for the in-app documentation engine
        # (Stage 3). The OpenAPI explorer lives under ``/api/v1/swagger``
        # to avoid the route collision.
        docs_url=f"{settings.api_root}/swagger",
        redoc_url=f"{settings.api_root}/redoc",
        openapi_url=f"{settings.api_root}/openapi.json",
        lifespan=lifespan,
    )

    install_middleware(app, settings)
    install_error_handlers(app)

    # Versioned API surface.
    app.include_router(api_v1_router, prefix=settings.api_root)
    app.include_router(ws_router, prefix=settings.api_root)

    # Optional SPA mount (production builds copy frontend dist into the image).
    if settings.frontend_dist and settings.frontend_dist.exists():
        _install_spa(app, settings)
    else:

        @app.get("/", include_in_schema=False)
        async def root() -> dict[str, str]:
            return {
                "name": "auditarr",
                "version": __version__,
                "api": settings.api_root,
                "swagger": f"{settings.api_root}/swagger",
                "docs": f"{settings.api_root}/docs",
            }

    return app


def _install_spa(app: FastAPI, settings: Settings) -> None:
    """Mount the built SPA with proper client-side-routing fallback.

    ``StaticFiles(html=True)`` only serves ``index.html`` for the root path —
    a hard-load of ``/login`` or ``/files/<id>`` would 404. We register a
    small Starlette middleware that, for any GET that would otherwise 404
    on a non-API path, serves the SPA's ``index.html`` instead.

    Real assets continue to be served from disk via the standard
    :class:`StaticFiles` mount.
    """
    from collections.abc import Awaitable, Callable

    from fastapi import Request, Response
    from fastapi.responses import FileResponse
    from starlette.middleware.base import BaseHTTPMiddleware

    dist = settings.frontend_dist
    assert dist is not None
    index_file = dist / "index.html"
    api_root = settings.api_root.rstrip("/")

    class SpaFallbackMiddleware(BaseHTTPMiddleware):
        async def dispatch(
            self,
            request: Request,
            call_next: Callable[[Request], Awaitable[Response]],
        ) -> Response:
            response = await call_next(request)
            if (
                response.status_code == 404
                and request.method == "GET"
                and not request.url.path.startswith(api_root)
                and "text/html" in request.headers.get("accept", "*/*")
                and index_file.is_file()
            ):
                return FileResponse(index_file)
            return response

    app.add_middleware(SpaFallbackMiddleware)

    # Real assets — fingerprinted JS/CSS/images.
    if (dist / "assets").exists():
        app.mount(
            "/assets",
            StaticFiles(directory=dist / "assets"),
            name="frontend-assets",
        )

    # Top-level files (favicon, robots.txt, manifest.webmanifest, …) and
    # ``/`` itself. ``html=True`` makes ``StaticFiles`` serve ``index.html``
    # for the bare root path; the SPA middleware above handles deeper paths.
    app.mount(
        "/",
        StaticFiles(directory=dist, html=True),
        name="frontend",
    )


app = create_app()
