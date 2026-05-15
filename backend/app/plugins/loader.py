"""Plugin discovery + lifecycle.

Discovery model
---------------
On startup the loader scans :attr:`Settings.plugin_dir` for direct
subdirectories. Each subdirectory must contain ``manifest.json``; the loader
validates the manifest, imports the backend entry as a sibling Python module
under a sandboxed namespace, calls its ``register(context)`` callable, and
stores the resulting :class:`Plugin` instance.

Plugins are loaded in dependency order (manifest ``requires`` → topological
sort). Cycles are reported and the offending plugins skipped.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import io
import json
import shutil
import sys
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PluginError,
    ValidationError as AppValidationError,
)
from app.core.logging import get_logger
from app.core.registry import ServiceRegistry, get_registry
from app.core.settings import Settings, get_settings
from app.events.bus import EventBus, get_event_bus
from app.plugins.contracts import Plugin, PluginContext, PluginManifest

if TYPE_CHECKING:
    from fastapi import FastAPI

log = get_logger("auditarr.plugins", category="plugin")

RegisterFn = Callable[[PluginContext], "Plugin | None | Awaitable[Plugin | None]"]


class LoadedPlugin:
    """Internal wrapper holding an instantiated plugin and its context."""

    def __init__(
        self,
        manifest: PluginManifest,
        context: PluginContext,
        instance: Plugin | None,
    ) -> None:
        self.manifest = manifest
        self.context = context
        self.instance = instance
        # Stage 25: track the most recent lifecycle error so the
        # plugins UI can surface "what went wrong" without operators
        # having to grep the log. Set by ``_run_lifecycle`` and by
        # ``_load_one`` when ``on_load`` raises. ``None`` means
        # "nothing has gone wrong on this plugin since it was
        # loaded" — which is the common case.
        self.last_error: str | None = None


class FailedLoad:
    """Stage 25: manifest the loader saw on disk but couldn't load.

    Distinct from :class:`LoadedPlugin` because there's no instance
    and no context to attach to — but the operator still needs the
    plugin's id, name (best-effort, from the manifest if it parsed),
    and the error message that prevented loading.
    """

    __slots__ = ("plugin_id", "manifest", "error", "directory")

    def __init__(
        self,
        *,
        plugin_id: str,
        manifest: PluginManifest | None,
        directory: Path,
        error: str,
    ) -> None:
        self.plugin_id = plugin_id
        self.manifest = manifest
        self.directory = directory
        self.error = error


class PluginLoader:
    """Discover and lifecycle-manage plugins."""

    def __init__(
        self,
        *,
        settings: Settings,
        registry: ServiceRegistry,
        event_bus: EventBus,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._bus = event_bus
        self._plugins: dict[str, LoadedPlugin] = {}
        # Stage 25: manifests we discovered but failed to load.
        # Keyed by plugin id (or directory name if even the manifest
        # didn't parse). Surfaced by ``list_summary`` so the UI can
        # show "failed_to_load" rows alongside loaded plugins.
        self._failed_loads: dict[str, FailedLoad] = {}
        # Bug-hunt 2: per-plugin reload mutex. Two concurrent
        # ``reload_one`` calls against the same plugin would race
        # — both run ``on_shutdown``/``on_unload``, both drop
        # the module from ``sys.modules``, both reimport, both
        # call ``on_startup``. Background tasks the first
        # ``on_startup`` registered survive while the second
        # ``on_startup`` runs on a fresh-but-conflicting copy of
        # the module. Net result: undefined event-bus
        # subscription accounting, dangling tasks, and (worst
        # case) a half-initialized ``instance`` exposed to other
        # callers via ``self._plugins``. The lock makes
        # reload-of-the-same-plugin serial; reload-across-
        # different-plugins still runs concurrently.
        self._reload_locks: dict[str, asyncio.Lock] = {}
        # Stage 10 (audit follow-up): strong references to fire-and-
        # forget background tasks spawned for plugin lifecycle hooks.
        # ``asyncio.create_task`` only keeps a weak reference; without
        # an external strong ref the task can be GC'd mid-flight and
        # silently vanish — exactly the long-uptime failure mode the
        # audit flagged. Tasks self-remove via ``add_done_callback``
        # so the set never grows unbounded.
        self._background_tasks: set[asyncio.Task[None]] = set()

    # ── Public lifecycle ───────────────────────────────────────
    async def discover_and_load(
        self,
        app: FastAPI | None = None,
        *,
        route_prefix: str = "",
    ) -> list[LoadedPlugin]:
        """Discover plugins on disk, validate, and load them in dep order.

        If *app* is provided and a plugin manifest declares ``routes: true``,
        the plugin's APIRouter is mounted on the app under ``route_prefix``.
        Pass the configured API root (e.g. ``/api/v1``) to keep plugin routes
        on the versioned API surface.
        """
        directories = self._settings.plugin_directories
        existing = [d for d in directories if d.exists()]
        if not existing:
            log.info(
                "plugin.dir_missing",
                paths=[str(d) for d in directories],
            )
            return []

        manifests: list[tuple[PluginManifest, Path]] = []
        seen_ids: set[str] = set()
        for directory in existing:
            for manifest, plugin_dir in self._discover_manifests(directory):
                if manifest.id in seen_ids:
                    log.warning(
                        "plugin.id_shadowed",
                        plugin=manifest.id,
                        path=str(plugin_dir),
                        reason="another plugin with the same id was loaded earlier",
                    )
                    continue
                seen_ids.add(manifest.id)
                manifests.append((manifest, plugin_dir))

        log.info(
            "plugin.discovered",
            count=len(manifests),
            roots=[str(d) for d in existing],
        )
        ordered = self._topo_sort(manifests)

        for manifest, directory in ordered:
            try:
                loaded = await self._load_one(manifest, directory)
                self._plugins[manifest.id] = loaded
                # If a prior attempt for the same id had failed,
                # clear the failed-load record now that it loaded
                # cleanly (matters for ``reload`` flows).
                self._failed_loads.pop(manifest.id, None)
                if app is not None and manifest.routes:
                    app.include_router(loaded.context.router, prefix=route_prefix)
                await self._bus.emit(
                    "plugin.loaded",
                    {
                        "id": manifest.id,
                        "version": manifest.version,
                        "type": manifest.type.value,
                    },
                    source="plugin-loader",
                )
            except Exception as exc:  # noqa: BLE001 — one bad plugin can't break boot
                log.error(
                    "plugin.load_failed",
                    plugin=manifest.id,
                    error=str(exc),
                    exc_info=True,
                )
                # Stage 25: remember the failure so the operator can
                # see it in the plugins table without grepping logs.
                self._failed_loads[manifest.id] = FailedLoad(
                    plugin_id=manifest.id,
                    manifest=manifest,
                    directory=directory,
                    error=str(exc),
                )
                await self._bus.emit(
                    "plugin.error",
                    {"id": manifest.id, "error": str(exc), "phase": "load"},
                    source="plugin-loader",
                )
        return list(self._plugins.values())

    # ── Lifecycle ────────────────────────────────────────────
    async def start(self) -> None:
        """Fire ``on_startup`` on every loaded plugin.

        Stage 12: this runs *after* the host is otherwise ready to serve
        traffic. Each plugin's ``on_startup`` is scheduled as a
        background task — we don't await completion, so a plugin that
        wants to run a forever-loop (poller, cache warmer) can simply
        do so inside ``on_startup`` without blocking host startup.

        Failures are logged + isolated; one plugin's faulty startup
        cannot prevent others from running.
        """
        for loaded in list(self._plugins.values()):
            await self._run_lifecycle(loaded, "on_startup", spawn=True)

    async def shutdown(self) -> None:
        """Fire ``on_shutdown`` then ``on_unload`` on every plugin.

        Both hooks are awaited; we want plugins to finish cleaning up
        before the host process exits. Failures are logged + isolated.
        """
        for loaded in list(self._plugins.values()):
            if loaded.instance is None:
                continue
            await self._run_lifecycle(loaded, "on_shutdown")

        for loaded in list(self._plugins.values()):
            if loaded.instance is None:
                continue
            await self._run_lifecycle(loaded, "on_unload")
            await self._bus.emit(
                "plugin.unloaded",
                {"id": loaded.manifest.id},
                source="plugin-loader",
            )
        self._plugins.clear()

    async def _run_lifecycle(
        self, loaded: LoadedPlugin, hook: str, *, spawn: bool = False
    ) -> None:
        """Invoke a single lifecycle hook with full error isolation.

        ``spawn=True`` schedules the hook as an asyncio task and returns
        immediately — used for ``on_startup`` so long-running setup
        doesn't block the host.
        """
        instance = loaded.instance
        if instance is None:
            return
        if getattr(instance, "_auditarr_lifecycle_failed", False):
            # An earlier lifecycle hook on this plugin failed; skip the
            # rest to avoid cascading errors.
            return
        method = getattr(instance, hook, None)
        if method is None:
            return

        async def _invoke() -> None:
            try:
                result = method()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "plugin.lifecycle_failed",
                    plugin=loaded.manifest.id,
                    hook=hook,
                    error=str(exc),
                )
                setattr(instance, "_auditarr_lifecycle_failed", True)
                # Stage 25: surface the lifecycle error on the wrapper
                # so list_summary can show it. Keeps the existing log
                # warning so log readers aren't affected.
                loaded.last_error = f"{hook}: {exc}"
                await self._bus.emit(
                    "plugin.error",
                    {
                        "id": loaded.manifest.id,
                        "error": str(exc),
                        "phase": hook,
                    },
                    source="plugin-loader",
                )

        if spawn:
            # Stage 10 (audit follow-up): hold a strong reference so
            # the task isn't GC'd before completion. The done-callback
            # cleans up on its own so the set doesn't grow unbounded.
            task = asyncio.create_task(
                _invoke(), name=f"plugin:{loaded.manifest.id}:{hook}"
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        else:
            await _invoke()

    # ── Introspection ─────────────────────────────────────────
    @property
    def plugins(self) -> dict[str, LoadedPlugin]:
        return dict(self._plugins)

    def list_summary(self) -> list[dict[str, object]]:
        """Return a UI-shaped summary of every plugin the loader knows
        about — both successfully loaded and failed-to-load.

        Stage 25: enriched with ``description``, ``author``, ``status``,
        and ``last_error``. ``status`` is one of:

        - ``loaded`` — instance present, no lifecycle failure recorded
        - ``errored`` — instance present but a lifecycle hook
          (``on_load`` / ``on_startup`` / …) raised; subsequent hooks
          are skipped (see ``_run_lifecycle``) and the plugin's
          contribution to capabilities is preserved or not depending
          on which hook failed
        - ``failed_to_load`` — manifest discovered but ``_load_one``
          raised before an instance could be returned; no routes are
          mounted and no capabilities are registered

        The order is: loaded/errored plugins first (in load order),
        then failed_to_load entries — so the table reads cleanly with
        problems collecting at the bottom rather than scrambling the
        usual top-of-list.
        """
        items: list[dict[str, object]] = []
        for p in self._plugins.values():
            instance = p.instance
            errored = (
                instance is not None
                and getattr(instance, "_auditarr_lifecycle_failed", False)
            )
            items.append(
                {
                    "id": p.manifest.id,
                    "name": p.manifest.name,
                    "version": p.manifest.version,
                    "type": p.manifest.type.value,
                    "description": p.manifest.description,
                    "author": p.manifest.author,
                    "capabilities": list(p.manifest.capabilities),
                    "routes": p.manifest.routes,
                    "has_settings": p.manifest.settings,
                    "status": "errored" if errored else "loaded",
                    "last_error": p.last_error,
                }
            )
        for fl in self._failed_loads.values():
            manifest = fl.manifest
            items.append(
                {
                    "id": fl.plugin_id,
                    "name": manifest.name if manifest else fl.plugin_id,
                    "version": manifest.version if manifest else "?",
                    "type": manifest.type.value if manifest else "unknown",
                    "description": manifest.description if manifest else "",
                    "author": manifest.author if manifest else "",
                    "capabilities": list(manifest.capabilities) if manifest else [],
                    "routes": manifest.routes if manifest else False,
                    "has_settings": manifest.settings if manifest else False,
                    "status": "failed_to_load",
                    "last_error": fl.error,
                }
            )
        return items

    # ── Stage 25: targeted reload ────────────────────────────
    async def reload_one(self, plugin_id: str) -> dict[str, object] | None:
        """Unload then reload a single plugin from disk.

        Returns the new summary entry for the reloaded plugin, or
        ``None`` if the plugin couldn't be found on disk.

        This is an explicit operator-triggered flow — it deliberately
        does NOT touch the host's lifecycle:

        - The plugin's ``on_shutdown``/``on_unload`` run if there's an
          existing instance.
        - The plugin's module is dropped from ``sys.modules`` so a
          re-import picks up source changes (operators reload
          precisely because they edited the plugin's code).
        - The manifest is re-read; bad manifests now land in
          ``_failed_loads`` and surface in ``list_summary``.
        - Routes that were mounted in the original load CANNOT be
          unmounted at runtime (FastAPI doesn't support removing
          routes from a running app), so a reload of a routed plugin
          warns and re-uses the existing router slot. Operators who
          add/remove routes still need a process restart for routing
          changes to take full effect; the existing routes will use
          the reloaded handler functions.

        Bug-hunt 2: a per-plugin asyncio.Lock serializes concurrent
        reload attempts on the same plugin. Without it, two rapid
        operator clicks could overlap shutdown/startup of the same
        plugin, leaving background tasks dangling or producing a
        half-initialized ``instance``.
        """
        # Lazily create the lock on first reload of this plugin.
        # asyncio.Lock construction is cheap and we don't want to
        # gate all loaders on a single global lock.
        lock = self._reload_locks.setdefault(plugin_id, asyncio.Lock())
        async with lock:
            return await self._reload_one_locked(plugin_id)

    async def _reload_one_locked(
        self, plugin_id: str
    ) -> dict[str, object] | None:
        """The real reload implementation; runs under the per-plugin
        lock acquired by :meth:`reload_one`."""
        existing = self._plugins.get(plugin_id)
        # If the plugin wasn't loaded but was a failed-load,
        # try to load it now.
        if existing is None and plugin_id not in self._failed_loads:
            return None

        # Tear down the existing instance if present.
        if existing is not None and existing.instance is not None:
            await self._run_lifecycle(existing, "on_shutdown")
            await self._run_lifecycle(existing, "on_unload")

        # Drop the module from sys.modules so the next import re-reads
        # the file from disk. The module name follows the same
        # convention used by ``_import_register``.
        module_name = f"auditarr_plugin_{plugin_id.replace('-', '_')}"
        sys.modules.pop(module_name, None)

        # Remove the existing wrapper before re-loading so a partial
        # failure leaves the loader in a clean state.
        self._plugins.pop(plugin_id, None)

        # Re-discover the manifest from disk. We don't trust the
        # cached manifest because the operator may have edited it.
        directory = (
            existing.context.directory
            if existing is not None
            else self._failed_loads[plugin_id].directory
        )
        manifest_path = directory / "manifest.json"
        try:
            raw = json.loads(manifest_path.read_text())
            manifest = PluginManifest.model_validate(raw)
        except Exception as exc:  # noqa: BLE001
            self._failed_loads[plugin_id] = FailedLoad(
                plugin_id=plugin_id,
                manifest=None,
                directory=directory,
                error=f"manifest reload failed: {exc}",
            )
            await self._bus.emit(
                "plugin.error",
                {"id": plugin_id, "error": str(exc), "phase": "reload"},
                source="plugin-loader",
            )
            return self._summary_for(plugin_id)

        try:
            loaded = await self._load_one(manifest, directory)
            self._plugins[manifest.id] = loaded
            self._failed_loads.pop(manifest.id, None)
            await self._bus.emit(
                "plugin.reloaded",
                {
                    "id": manifest.id,
                    "version": manifest.version,
                    "type": manifest.type.value,
                },
                source="plugin-loader",
            )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "plugin.reload_failed",
                plugin=plugin_id,
                error=str(exc),
                exc_info=True,
            )
            self._failed_loads[plugin_id] = FailedLoad(
                plugin_id=plugin_id,
                manifest=manifest,
                directory=directory,
                error=str(exc),
            )
            await self._bus.emit(
                "plugin.error",
                {"id": plugin_id, "error": str(exc), "phase": "reload"},
                source="plugin-loader",
            )

        return self._summary_for(plugin_id)

    # ── Stage 32: install from zip + uninstall ─────────────────
    async def install_from_zip(
        self,
        zip_bytes: bytes,
        *,
        app: FastAPI | None = None,
        route_prefix: str = "",
    ) -> dict[str, object]:
        """Install a plugin from a zipped archive.

        The zip is expected to contain a single top-level directory
        with a ``manifest.yaml`` (or ``manifest.json``) at its root,
        a ``backend.py`` (or whatever the manifest's ``entry`` points
        at), plus any auxiliary files the plugin needs.

        Steps:

          1. Open the zip in memory and locate the manifest entry.
          2. Parse + validate the manifest (raises ``ValidationError``
             on schema problems, before anything touches disk).
          3. Refuse if a plugin with that id is already loaded —
             the operator should ``uninstall`` first, or use
             ``reload`` if they just want to swap files in-place.
             ``ConflictError`` → 409 at the API layer.
          4. Extract to ``settings.plugin_directories[0] /
             <plugin_id>/`` (the FIRST configured directory; if
             multiple are configured, this is the canonical install
             target).
          5. Call ``_load_one`` to instantiate and run lifecycle.
          6. Mount routes if the manifest declares ``routes: true``.
          7. Return the standard summary dict (same shape as
             ``reload_one``).

        Errors are surfaced as raised exceptions; the caller is
        the FastAPI router which translates each to an HTTP status.
        """
        # 1 + 2: parse the manifest from inside the zip without
        # touching disk yet. That way a bad upload (wrong schema,
        # missing manifest, malformed zip) doesn't leave half a
        # directory tree behind.
        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except zipfile.BadZipFile as exc:
            raise AppValidationError(
                f"Uploaded file is not a valid zip archive: {exc}"
            ) from exc

        try:
            manifest_member, top_level = self._find_manifest_in_zip(zf)
            manifest_data = self._parse_manifest_bytes(
                zf.read(manifest_member.filename), manifest_member.filename
            )
            try:
                manifest = PluginManifest.model_validate(manifest_data)
            except ValidationError as exc:
                raise AppValidationError(
                    f"Plugin manifest is invalid: {exc}"
                ) from exc

            # 3: id collision check. Check both currently-loaded
            # and previously-failed-load records — both block
            # re-install with the same id.
            if manifest.id in self._plugins:
                raise ConflictError(
                    f"A plugin with id {manifest.id!r} is already "
                    "installed. Uninstall it first, or use reload "
                    "to swap files in place."
                )
            if manifest.id in self._failed_loads:
                raise ConflictError(
                    f"A plugin with id {manifest.id!r} previously "
                    "failed to load. Uninstall it first (the "
                    "uninstall endpoint accepts failed-load plugins)."
                )

            # 4: extract under a per-id lock so two simultaneous
            # uploads of the same plugin id can't both win the
            # disk write race.
            lock = self._reload_locks.setdefault(manifest.id, asyncio.Lock())
            async with lock:
                directory = await self._extract_zip_to_plugin_dir(
                    zf, manifest.id, top_level
                )

            # 5: load it. From here on, _load_one's lifecycle
            # handling takes over — including surfacing on_load
            # failures via last_error rather than blowing up the
            # caller.
            try:
                loaded = await self._load_one(manifest, directory)
            except Exception:
                # Lifecycle/import failed AFTER extraction. Roll
                # back the disk write so the operator isn't left
                # with a half-installed plugin that they then have
                # to manually clean up. The exception still
                # propagates so the API returns the failure.
                shutil.rmtree(directory, ignore_errors=True)
                raise

            self._plugins[manifest.id] = loaded
            self._failed_loads.pop(manifest.id, None)
            if app is not None and manifest.routes:
                app.include_router(
                    loaded.context.router, prefix=route_prefix
                )
            await self._bus.emit(
                "plugin.installed",
                {
                    "id": manifest.id,
                    "version": manifest.version,
                    "source": "upload",
                },
                source="plugin-loader",
            )
            log.info(
                "plugin.installed",
                plugin=manifest.id,
                version=manifest.version,
                path=str(directory),
            )
            summary = self._summary_for(manifest.id)
            assert summary is not None  # we just added it
            return summary
        finally:
            zf.close()

    def _find_manifest_in_zip(
        self, zf: zipfile.ZipFile
    ) -> tuple[zipfile.ZipInfo, str]:
        """Locate manifest.yaml or manifest.json in the zip.

        Returns the ZipInfo + the top-level directory name (so we
        can rebuild the extracted path without including the
        archive's wrapper dir). Raises ``ValidationError`` if no
        manifest is present or if multiple top-level dirs exist.

        Accepted layouts:
          - ``<plugin-id>/manifest.yaml`` (canonical)
          - ``<plugin-id>/manifest.json``
        Rejected:
          - manifest at the zip root with no wrapper dir
          - multiple top-level directories
          - paths containing ``..`` (zip slip protection)
        """
        members = zf.infolist()
        if not members:
            raise AppValidationError("Plugin zip is empty")

        # Zip slip protection: no member may resolve outside its
        # intended directory. ``..`` in a path segment is the
        # canonical attack; we reject the upload outright rather
        # than try to sanitize.
        for m in members:
            normalized = Path(m.filename).as_posix()
            if ".." in normalized.split("/") or normalized.startswith("/"):
                raise AppValidationError(
                    f"Plugin zip contains an unsafe path: {m.filename!r}"
                )

        # Find the top-level directories.
        top_levels = {
            m.filename.split("/", 1)[0]
            for m in members
            if "/" in m.filename
        }
        if len(top_levels) == 0:
            raise AppValidationError(
                "Plugin zip must contain a top-level directory "
                "(found only loose files)"
            )
        if len(top_levels) > 1:
            raise AppValidationError(
                "Plugin zip must contain exactly one top-level "
                f"directory; found {len(top_levels)}: "
                + ", ".join(sorted(top_levels))
            )
        top_level = next(iter(top_levels))

        for candidate in ("manifest.yaml", "manifest.yml", "manifest.json"):
            try:
                info = zf.getinfo(f"{top_level}/{candidate}")
                return info, top_level
            except KeyError:
                continue
        raise AppValidationError(
            f"Plugin zip {top_level!r} has no manifest.yaml or "
            "manifest.json at its root"
        )

    def _parse_manifest_bytes(
        self, raw: bytes, filename: str
    ) -> dict[str, object]:
        """Parse manifest bytes as YAML or JSON depending on extension."""
        if filename.endswith(".json"):
            return json.loads(raw.decode("utf-8"))
        # yaml.safe_load handles both YAML and JSON.
        import yaml  # noqa: PLC0415 — lazy import; yaml is a runtime dep but heavy

        result = yaml.safe_load(raw.decode("utf-8"))
        if not isinstance(result, dict):
            raise AppValidationError(
                "Plugin manifest must be a mapping, not "
                f"{type(result).__name__}"
            )
        return result

    async def _extract_zip_to_plugin_dir(
        self,
        zf: zipfile.ZipFile,
        plugin_id: str,
        top_level: str,
    ) -> Path:
        """Extract the zip's top-level dir contents to the user-
        supplied plugin install location. Returns the install
        directory.

        Bug-hunt note: write to ``settings.plugin_dir`` (the
        operator-managed directory) NOT
        ``settings.plugin_directories[0]`` — the latter is
        ``builtin_plugin_dir`` (shipped reference plugins). We
        never want operator uploads to land in the same directory
        as Auditarr's first-party reference plugins; mixing them
        makes "which plugins did I install vs which ship with the
        product" impossible to answer from disk.

        We rename from the zip's wrapper dir (``<top_level>/``)
        to ``<plugin_id>/`` so the on-disk name always matches
        the manifest id — operators don't need to zip with a
        specific wrapper name.

        Rejects installation if the target already exists. The
        caller's id-collision check guards the in-memory case;
        this check guards the disk-only edge case where the
        directory exists but no plugin was loaded from it (e.g.
        an old uninstall that left a tombstone, or manual ``rm``
        from the registry).
        """
        target_root = self._settings.plugin_dir
        target_root.mkdir(parents=True, exist_ok=True)
        target = target_root / plugin_id
        if target.exists():
            raise ConflictError(
                f"Plugin directory already exists at {target}. "
                "Remove it manually or uninstall the plugin if it's "
                "loaded."
            )

        # Extract everything under top_level into target. Strip
        # the top_level prefix so the install dir is named after
        # the plugin id, not the archive's wrapper.
        prefix = f"{top_level}/"

        # Bug-hunt 3: zip bomb protection. The 16 MiB limit on
        # the compressed upload (enforced by the API endpoint)
        # doesn't protect against high-compression-ratio
        # payloads — a 1 MiB zip can decompress to many GiB and
        # fill the operator's disk. Sum the uncompressed sizes
        # from the zip's metadata BEFORE we write anything; bail
        # if the total exceeds 128 MiB (8x the upload cap; well
        # past any reasonable plugin).
        #
        # ``member.file_size`` is the value the archive
        # *claims*; it's the same value Python's zipfile uses
        # to skip past member contents during ``infolist()``.
        # A maliciously crafted archive could lie about
        # uncompressed size in the central directory but match a
        # different value during streaming; we belt-and-suspenders
        # this by also counting bytes during the extraction loop
        # and aborting if the running total exceeds the cap.
        MAX_EXTRACTED_BYTES = 128 * 1024 * 1024
        claimed_total = 0
        for member in zf.infolist():
            if member.filename.startswith(prefix):
                claimed_total += member.file_size
        if claimed_total > MAX_EXTRACTED_BYTES:
            raise AppValidationError(
                f"Plugin zip would expand to "
                f"{claimed_total // (1024 * 1024)} MiB "
                f"(cap: {MAX_EXTRACTED_BYTES // (1024 * 1024)} "
                "MiB). Auditarr plugins are typically a few MiB; "
                "this archive is almost certainly a zip bomb or a "
                "wrong upload."
            )

        target.mkdir()
        bytes_written = 0
        try:
            for member in zf.infolist():
                if not member.filename.startswith(prefix):
                    continue
                relative = member.filename[len(prefix):]
                if not relative:
                    continue  # skip the wrapper dir entry itself
                dest = target / relative
                if member.is_dir():
                    dest.mkdir(parents=True, exist_ok=True)
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, dest.open("wb") as out:
                    # Streamed copy with running byte count. If
                    # the archive lied about uncompressed sizes
                    # in its central directory, we still abort
                    # mid-stream rather than fill the disk.
                    while True:
                        chunk = src.read(64 * 1024)
                        if not chunk:
                            break
                        bytes_written += len(chunk)
                        if bytes_written > MAX_EXTRACTED_BYTES:
                            raise AppValidationError(
                                "Plugin zip exceeds the "
                                f"{MAX_EXTRACTED_BYTES // (1024 * 1024)} "
                                "MiB expansion cap during extraction "
                                "(the archive's claimed size was "
                                "smaller — likely a zip bomb)."
                            )
                        out.write(chunk)
        except Exception:
            # If extraction blew up partway through, clean up the
            # half-extracted directory rather than leave a mess.
            shutil.rmtree(target, ignore_errors=True)
            raise
        return target

    async def uninstall(self, plugin_id: str) -> dict[str, object]:
        """Uninstall a plugin: run lifecycle teardown, delete files
        from disk, drop loader state.

        Acquires the per-plugin lock to serialize against any
        concurrent ``reload`` or ``install`` for the same id (which
        would otherwise race on ``self._plugins``).

        Plugin settings rows in the database persist across
        uninstall — re-installing the same plugin id later picks
        them up automatically. This is intentional: the operator
        almost always wants their configuration back when they
        re-install. Settings can be cleared via the existing
        ``DELETE /plugins/{id}/settings`` endpoint if desired.

        Routes mounted by the plugin during its original load
        CANNOT be unmounted at runtime (FastAPI doesn't support
        route removal). The route handlers will still exist after
        uninstall but will fail at the import boundary because the
        plugin's module has been dropped from sys.modules. A
        process restart fully reclaims the route table.

        Returns a small status payload: ``{"id": ..., "removed":
        True, "warnings": [...]}``. Warnings include the "routes
        not unmountable" note when the plugin had declared routes.
        """
        # Look up first to bail early with a clean 404 if unknown.
        loaded = self._plugins.get(plugin_id)
        failed = self._failed_loads.get(plugin_id)
        if loaded is None and failed is None:
            raise NotFoundError(
                f"Plugin {plugin_id!r} is not installed"
            )

        lock = self._reload_locks.setdefault(plugin_id, asyncio.Lock())
        async with lock:
            warnings: list[str] = []
            # Recompute under the lock — another caller could have
            # uninstalled while we were waiting.
            loaded = self._plugins.get(plugin_id)
            failed = self._failed_loads.get(plugin_id)
            if loaded is None and failed is None:
                raise NotFoundError(
                    f"Plugin {plugin_id!r} is not installed"
                )

            directory: Path | None = None
            had_routes = False

            if loaded is not None:
                directory = loaded.context.directory
                had_routes = bool(loaded.manifest.routes)
                if loaded.instance is not None:
                    await self._run_lifecycle(loaded, "on_shutdown")
                    await self._run_lifecycle(loaded, "on_unload")
                # Drop the module from sys.modules so a later
                # re-install picks up fresh code rather than a
                # cached import.
                module_name = (
                    f"auditarr_plugin_{plugin_id.replace('-', '_')}"
                )
                sys.modules.pop(module_name, None)
            elif failed is not None:
                directory = failed.directory

            # Remove from loader state BEFORE touching disk, so a
            # mid-removal failure doesn't leave a "loaded" record
            # pointing at a partially-deleted directory.
            self._plugins.pop(plugin_id, None)
            self._failed_loads.pop(plugin_id, None)

            # Delete the directory. Use ``ignore_errors=True``
            # because some files may be locked on Windows; the
            # operator gets a warning, the loader state is still
            # clean, and a follow-up restart can finish the
            # cleanup. Better than failing the whole uninstall
            # and leaving the loader in an inconsistent state.
            if directory is not None and directory.exists():
                shutil.rmtree(directory, ignore_errors=True)
                if directory.exists():
                    warnings.append(
                        f"Plugin directory at {directory} could "
                        "not be fully removed (some files may be "
                        "locked). The plugin is unloaded; a "
                        "restart will clear the rest."
                    )

            if had_routes:
                warnings.append(
                    "Routes mounted by this plugin cannot be "
                    "unregistered at runtime. They will return "
                    "import errors until the next process restart."
                )

            await self._bus.emit(
                "plugin.uninstalled",
                {"id": plugin_id},
                source="plugin-loader",
            )
            log.info(
                "plugin.uninstalled",
                plugin=plugin_id,
                directory=str(directory) if directory else None,
            )

            return {
                "id": plugin_id,
                "removed": True,
                "warnings": warnings,
            }

    def _summary_for(self, plugin_id: str) -> dict[str, object] | None:
        for item in self.list_summary():
            if item["id"] == plugin_id:
                return item
        return None

    # ── Discovery ─────────────────────────────────────────────
    def _discover_manifests(
        self, root: Path
    ) -> list[tuple[PluginManifest, Path]]:
        out: list[tuple[PluginManifest, Path]] = []
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith((".", "_")):
                continue
            manifest_path = child / "manifest.json"
            if not manifest_path.exists():
                log.debug("plugin.skip_no_manifest", path=str(child))
                continue
            try:
                raw = json.loads(manifest_path.read_text())
                manifest = PluginManifest.model_validate(raw)
            except (json.JSONDecodeError, ValidationError) as exc:
                log.error(
                    "plugin.manifest_invalid",
                    path=str(manifest_path),
                    error=str(exc),
                )
                continue
            out.append((manifest, child))
        return out

    def _topo_sort(
        self, items: list[tuple[PluginManifest, Path]]
    ) -> list[tuple[PluginManifest, Path]]:
        by_id = {m.id: (m, d) for m, d in items}
        order: list[tuple[PluginManifest, Path]] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_id: str, stack: list[str]) -> None:
            if node_id in visited:
                return
            if node_id in visiting:
                cycle = " -> ".join([*stack, node_id])
                raise PluginError(
                    f"Plugin dependency cycle detected: {cycle}",
                    details={"cycle": cycle},
                )
            if node_id not in by_id:
                log.warning("plugin.missing_dependency", required=node_id)
                return
            visiting.add(node_id)
            manifest, directory = by_id[node_id]
            for req in manifest.requires:
                visit(req, [*stack, node_id])
            visiting.discard(node_id)
            visited.add(node_id)
            order.append((manifest, directory))

        for manifest, _ in items:
            try:
                visit(manifest.id, [])
            except PluginError as exc:
                log.error("plugin.toposort_failed", error=str(exc))
        return order

    # ── Single-plugin load ────────────────────────────────────
    async def _load_one(
        self, manifest: PluginManifest, directory: Path
    ) -> LoadedPlugin:
        context = PluginContext(
            manifest=manifest,
            directory=directory,
            registry=self._registry,
            event_bus=self._bus,
        )
        register_fn = self._import_register(manifest, directory)

        result = register_fn(context)
        if inspect.isawaitable(result):
            result = await result
        instance = result if isinstance(result, Plugin) else None

        loaded_wrapper = LoadedPlugin(
            manifest=manifest, context=context, instance=instance
        )

        if instance is not None:
            try:
                await instance.on_load()
            except Exception as exc:  # noqa: BLE001
                # Stage 12: a faulty ``on_load`` no longer crashes the
                # whole loader run. The plugin is still considered
                # loaded (its register() succeeded and any capabilities
                # are wired up); we just record the lifecycle failure.
                # Subsequent lifecycle hooks (``on_startup`` etc.) won't
                # run for this plugin — see ``_run_lifecycle``.
                log.warning(
                    "plugin.on_load_failed",
                    plugin=manifest.id,
                    error=str(exc),
                )
                # Annotate the instance so later hooks can be skipped.
                setattr(instance, "_auditarr_lifecycle_failed", True)
                # Stage 25: surface to the operator via list_summary.
                loaded_wrapper.last_error = f"on_load: {exc}"

        # Auto-register declared capabilities only if the plugin already used the SDK.
        # (Capabilities not registered remain available for the plugin to claim later.)
        log.info(
            "plugin.loaded",
            plugin=manifest.id,
            version=manifest.version,
            type=manifest.type.value,
        )
        return loaded_wrapper

    def _import_register(self, manifest: PluginManifest, directory: Path) -> RegisterFn:
        entry_path = directory / manifest.backend_entry
        if not entry_path.exists():
            raise PluginError(
                f"Plugin {manifest.id!r} backend_entry not found: {entry_path.name}"
            )

        module_name = f"auditarr_plugin_{manifest.id.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, entry_path)
        if spec is None or spec.loader is None:
            raise PluginError(
                f"Cannot load plugin module for {manifest.id!r}"
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001
            sys.modules.pop(module_name, None)
            raise PluginError(
                f"Plugin {manifest.id!r} failed to import: {exc}",
                cause=exc,
            ) from exc

        register_fn = getattr(module, "register", None)
        if not callable(register_fn):
            raise PluginError(
                f"Plugin {manifest.id!r} backend_entry must define `register(context)`"
            )
        return register_fn  # type: ignore[return-value]


_loader: PluginLoader | None = None


def get_plugin_loader() -> PluginLoader:
    """Return the process-wide plugin loader singleton."""
    global _loader
    if _loader is None:
        _loader = PluginLoader(
            settings=get_settings(),
            registry=get_registry(),
            event_bus=get_event_bus(),
        )
    return _loader
