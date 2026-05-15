"""Plugin introspection, settings, and gallery endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, UploadFile

from app.api.auth_deps import AdminUser, CurrentUser
from app.api.dependencies import (
    PluginLoaderDep,
    SessionDep,
    SettingsDep,
)
from app.core.exceptions import NotFoundError, ValidationError
from app.plugins.gallery import fetch_gallery
from app.schemas.plugins import (
    GalleryFetchResult,
    GalleryPluginEntry,
    PluginSettingsRead,
    PluginSettingsSchema,
    PluginSettingsWrite,
)
from app.services.plugin_settings import PluginSettingsService

router = APIRouter(prefix="/plugins", tags=["plugins"])


# ── Listing ────────────────────────────────────────────────────
@router.get("", summary="List loaded plugins")
async def list_plugins(loader: PluginLoaderDep) -> list[dict[str, Any]]:
    """Stage 25: summary entries now carry ``description``, ``author``,
    ``status``, and ``last_error``. ``status`` is one of ``loaded``,
    ``errored`` (loaded but a lifecycle hook raised), or
    ``failed_to_load`` (manifest discovered, instance never created).
    The shape is dict-typed (not a Pydantic model) to keep the loader
    flexible — adding a new summary field doesn't require a schema
    change. Existing fields are preserved.
    """
    return loader.list_summary()


# ── Gallery ────────────────────────────────────────────────────
# Stage 12 — IMPORTANT: ``/gallery`` is a literal segment and must be
# declared before any ``/{plugin_id}/...`` route or FastAPI's path-param
# matching swallows it.
@router.get(
    "/gallery",
    response_model=GalleryFetchResult,
    summary="Browse the operator-configured plugin directory",
)
async def get_gallery(
    _user: CurrentUser, loader: PluginLoaderDep, settings: SettingsDep
) -> GalleryFetchResult:
    url = (settings.plugin_gallery_url or "").strip()
    if not url:
        return GalleryFetchResult(
            ok=False, feed_url="", detail="gallery disabled"
        )
    feed = await fetch_gallery(url)
    if not feed.ok:
        return GalleryFetchResult(ok=False, feed_url=url, detail=feed.detail)
    installed_ids = set(loader.plugins.keys())
    entries = [
        GalleryPluginEntry(
            id=p.id,
            name=p.name,
            description=p.description,
            author=p.author,
            version=p.version,
            source_url=p.source_url,
            install_url=p.install_url,
            install_instructions=p.install_instructions,
            categories=p.categories,
            installed=p.id in installed_ids,
        )
        for p in feed.plugins
    ]
    return GalleryFetchResult(ok=True, feed_url=url, plugins=entries)


# ── Per-plugin settings ────────────────────────────────────────
@router.get(
    "/{plugin_id}/settings/schema",
    response_model=PluginSettingsSchema,
    summary="JSON Schema for a plugin's settings (if it declares one)",
)
async def get_settings_schema(
    plugin_id: str,
    _user: CurrentUser,
    session: SessionDep,
) -> PluginSettingsSchema:
    service = PluginSettingsService(session)
    schema = service.schema_for(plugin_id)
    defaults: dict[str, Any] | None = None
    if schema is not None:
        defaults = await service.values_or_defaults(plugin_id)
    return PluginSettingsSchema(plugin_id=plugin_id, schema=schema, defaults=defaults)


@router.get(
    "/{plugin_id}/settings",
    response_model=PluginSettingsRead | None,
    summary="Read the persisted settings for a plugin",
)
async def get_settings(
    plugin_id: str,
    _user: CurrentUser,
    session: SessionDep,
) -> PluginSettingsRead | None:
    service = PluginSettingsService(session)
    row = await service.get(plugin_id)
    if row is None:
        return None
    return PluginSettingsRead.model_validate(row)


@router.put(
    "/{plugin_id}/settings",
    response_model=PluginSettingsRead,
    summary="Persist settings for a plugin (admin)",
)
async def put_settings(
    plugin_id: str,
    body: PluginSettingsWrite,
    _admin: AdminUser,
    session: SessionDep,
) -> PluginSettingsRead:
    service = PluginSettingsService(session)
    row = await service.upsert(
        plugin_id=plugin_id, values=body.values, notes=body.notes
    )
    await session.commit()
    return PluginSettingsRead.model_validate(row)


# ── Stage 32: install from upload ──────────────────────────────
# IMPORTANT: ``/install`` is a literal segment; declared BEFORE any
# ``/{plugin_id}/...`` route so FastAPI's path matching doesn't
# treat it as a plugin id (same convention as ``/gallery`` above).
# Bound for installations of newly-uploaded plugins; for installs
# from a gallery entry, a future ``/install-from-gallery`` endpoint
# will reuse the same loader path.
@router.post(
    "/install",
    summary="Install a plugin from an uploaded zip (admin)",
)
async def install_plugin(
    file: UploadFile,
    request: Request,
    _admin: AdminUser,
    loader: PluginLoaderDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Upload + install a plugin in one step.

    Accepts a single ``file`` form field carrying a zip archive
    with the layout described in :meth:`PluginLoader.install_from_zip`.

    The endpoint enforces a max upload size of 16 MiB — generous
    for any reasonable plugin (the largest first-party plugins are
    well under 1 MiB) while still rejecting accidental uploads of
    multi-hundred-MiB media files. The check happens BEFORE the
    bytes are passed to the loader so a too-large upload never
    sits in process memory.

    On success the loader emits ``plugin.installed`` and the
    response carries the same summary dict that ``GET /plugins``
    rows do, so the UI can splice the new row straight into its
    table without a re-fetch.
    """
    # Read the entire upload into memory. 16 MiB is well within
    # FastAPI's defaults; we cap explicitly so a misbehaving
    # client can't OOM the server with a hostile chunked upload.
    MAX_BYTES = 16 * 1024 * 1024
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValidationError(
            f"Plugin upload exceeds the {MAX_BYTES // (1024 * 1024)} "
            "MiB limit. Auditarr plugins are typically well under "
            "1 MiB; uploads this large are almost always a mistake."
        )
    if not data:
        raise ValidationError("Uploaded file is empty")

    # Hand off to the loader. ``request.app`` is the FastAPI app
    # the route is currently mounted on — passing it lets the
    # loader include the plugin's APIRouter on the live app for
    # plugins that declare ``routes: true``.
    summary = await loader.install_from_zip(
        data, app=request.app, route_prefix="/api/v1"
    )
    return summary


# ── Stage 25: reload a single plugin ──────────────────────────
@router.post(
    "/{plugin_id}/reload",
    summary="Reload a single plugin's module and re-run on_load (admin)",
)
async def reload_plugin(
    plugin_id: str,
    _admin: AdminUser,
    loader: PluginLoaderDep,
) -> dict[str, Any]:
    """Reload a plugin from disk without restarting the host process.

    Use case: the operator edits a plugin's ``backend.py`` and wants
    the new code to be live without a full restart. Tears down the
    existing instance via ``on_shutdown`` / ``on_unload``, drops the
    module from ``sys.modules``, re-reads the manifest, and re-runs
    ``register()`` + ``on_load()``.

    Caveat — and this is intentional and worth knowing: routes
    mounted by a plugin during the original load CANNOT be
    unregistered at runtime (FastAPI doesn't support route removal).
    For a routed plugin, reloading swaps the in-memory module so the
    existing route handlers pick up code changes, but adding /
    removing routes still needs a process restart. The reload
    response surfaces the plugin's new status so the operator can
    see whether the reloaded ``on_load`` succeeded.
    """
    summary = await loader.reload_one(plugin_id)
    if summary is None:
        raise NotFoundError(f"Plugin {plugin_id!r} not known to the loader")
    return summary


# ── Stage 32: uninstall ────────────────────────────────────────
@router.delete(
    "/{plugin_id}",
    summary="Uninstall a plugin and delete its files from disk (admin)",
)
async def uninstall_plugin(
    plugin_id: str,
    _admin: AdminUser,
    loader: PluginLoaderDep,
) -> dict[str, Any]:
    """Symmetric to ``/plugins/install``: tears down the plugin's
    lifecycle, drops it from the loader's registries, and deletes
    its directory from disk.

    Plugin **settings** rows in the database persist across
    uninstall. Re-installing the same plugin id picks them up
    automatically — operators almost always want their config
    back after re-install. Use ``DELETE /plugins/{id}/settings``
    (a future endpoint) to clear settings if desired.

    Returns a small status payload with any warnings: the most
    common warning is "routes mounted by this plugin cannot be
    unregistered at runtime" for routed plugins. The route
    handlers will return errors after uninstall until a process
    restart fully reclaims the route table.
    """
    return await loader.uninstall(plugin_id)


@router.get(
    "/{plugin_id}",
    summary="Get a single loaded plugin's metadata",
)
async def get_plugin(
    plugin_id: str, loader: PluginLoaderDep
) -> dict[str, Any]:
    summary = next(
        (p for p in loader.list_summary() if p["id"] == plugin_id),
        None,
    )
    if summary is None:
        raise NotFoundError(f"Plugin {plugin_id!r} not loaded")
    return summary
