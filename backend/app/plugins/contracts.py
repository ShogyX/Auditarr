"""Plugin contracts.

A plugin is a directory under the configured plugin root containing a
``manifest.json`` and a Python entrypoint. Plugins NEVER touch the database
session, repositories, or frontend stores directly — they communicate
through the :class:`PluginContext` SDK only.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    import httpx

    from app.core.registry import ServiceRegistry
    from app.events.bus import EventBus
    from app.integrations.types import IntegrationProvider
    from app.notifications.types import NotificationProvider

# Slug = lowercase alnum + dash, 2–48 chars. Plugin ids and capability namespaces.
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,47}$")


class PluginType(StrEnum):
    """Categories of plugin understood by the loader."""

    INTEGRATION = "integration"
    NOTIFICATION = "notification"
    OPTIMIZATION = "optimization"
    RULE = "rule"
    WIDGET = "widget"
    DOCS = "docs"
    GENERIC = "generic"


class PluginManifest(BaseModel):
    """The on-disk ``manifest.json`` schema."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: str
    type: PluginType = PluginType.GENERIC
    description: str = ""
    author: str = ""

    backend_entry: str = "backend.py"
    frontend_entry: str | None = None

    routes: bool = False
    navigation: bool = False
    settings: bool = False

    permissions: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    requires: list[str] = Field(
        default_factory=list,
        description="other plugin ids that must load first",
    )

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(
                "Plugin id must be lowercase, start with a letter, and contain only "
                "letters, digits, or dashes (2–48 chars)"
            )
        return v

    @field_validator("version")
    @classmethod
    def _validate_version(cls, v: str) -> str:
        if not re.match(r"^\d+\.\d+\.\d+(-[a-z0-9.-]+)?$", v):
            raise ValueError("Plugin version must be semver (X.Y.Z[-pre])")
        return v

    @field_validator("capabilities")
    @classmethod
    def _validate_capabilities(cls, v: list[str]) -> list[str]:
        for cap in v:
            if "." not in cap or not all(part for part in cap.split(".")):
                raise ValueError(
                    f"Capability must be dotted lowercase (got {cap!r})"
                )
        return v


class PluginContext:
    """Stable SDK exposed to plugins.

    This is the only surface a plugin should touch. Anything not exposed here
    is private core infrastructure and not part of the plugin contract.
    """

    def __init__(
        self,
        *,
        manifest: PluginManifest,
        directory: Path,
        registry: ServiceRegistry,
        event_bus: EventBus,
    ) -> None:
        self.manifest = manifest
        self.directory = directory
        self._registry = registry
        self._event_bus = event_bus
        self.router: APIRouter = APIRouter(
            prefix=f"/plugins/{manifest.id}",
            tags=[f"plugin:{manifest.id}"],
        )

    # ── Capabilities ────────────────────────────────────────────
    def register_capability(self, capability: str, provider: object) -> None:
        if capability not in self.manifest.capabilities:
            raise ValueError(
                f"Plugin {self.manifest.id!r} did not declare capability {capability!r}"
            )
        self._registry.register_capability(capability, provider)

    # ── Integrations (Stage 5) ──────────────────────────────────
    def register_integration(self, provider: "IntegrationProvider") -> None:
        """Register this plugin as an integration provider.

        The integration manager looks up providers by ``provider.kind`` (which
        must equal the plugin's manifest id) and dispatches healthchecks,
        polls, and library auto-discovery to them.
        """
        if provider.kind != self.manifest.id:
            raise ValueError(
                f"IntegrationProvider.kind ({provider.kind!r}) must match "
                f"plugin id ({self.manifest.id!r})"
            )
        self._registry.register_capability(
            f"integration.{provider.kind}", provider
        )

    # ── Notifications (Stage 9) ─────────────────────────────────
    def register_notification_channel(
        self, provider: "NotificationProvider"
    ) -> None:
        """Register this plugin as a notification channel provider.

        Unlike integrations, notification channels do not require the
        provider's ``kind`` to match the plugin id — a plugin may register
        several channel kinds (e.g. one plugin exposing ``ntfy`` and
        ``gotify`` together). The dispatcher looks providers up by
        ``provider.kind``; plugins should pick distinctive, prefixed names
        to avoid colliding with the built-ins (``email``, ``webhook``,
        ``discord``, ``slack``, ``apprise``).
        """
        self._registry.register_capability(
            "notifications.channel", provider
        )

    # ── HTTP client factory ─────────────────────────────────────
    def http_client(
        self,
        *,
        base_url: str | None = None,
        timeout: float = 15.0,
        headers: dict[str, str] | None = None,
    ) -> "httpx.AsyncClient":
        """Build an :class:`httpx.AsyncClient` with sensible defaults.

        Plugins should ``async with`` the result so the connection pool is
        cleaned up promptly.
        """
        import httpx

        return httpx.AsyncClient(
            base_url=base_url or "",
            timeout=timeout,
            headers=headers,
            follow_redirects=True,
        )

    # ── Events ──────────────────────────────────────────────────
    @property
    def events(self) -> EventBus:
        return self._event_bus

    # ── Logging ─────────────────────────────────────────────────
    def logger(self) -> object:
        from app.core.logging import get_logger

        return get_logger(
            f"auditarr.plugin.{self.manifest.id}",
            category="plugin",
            plugin_id=self.manifest.id,
            plugin_version=self.manifest.version,
        )


class Plugin:
    """Base class plugins extend.

    A plugin module must define a top-level callable named :func:`register`
    accepting a :class:`PluginContext`. Subclassing :class:`Plugin` is optional
    but recommended for lifecycle hooks.

    Lifecycle (in firing order):

    1. ``register(context)`` — called once at load time. The plugin
       declares capabilities via the context SDK. Returning a
       :class:`Plugin` subclass instance opts the plugin in to the rest
       of the lifecycle.
    2. :meth:`on_load` — called once immediately after ``register``.
       Lightweight setup; should not perform long-running work.
    3. :meth:`on_startup` — called after **all** plugins have loaded and
       the application is otherwise ready to serve traffic. Use this for
       long-running background tasks; the loader spawns ``on_startup`` in
       a background task and never blocks the host on it.
    4. :meth:`on_shutdown` — called during graceful shutdown, before
       :meth:`on_unload`. Use this to cancel background tasks cleanly.
    5. :meth:`on_unload` — called last, after all plugins' ``on_shutdown``
       have completed. Use this for "release any resources I'm still
       holding" cleanup.

    Any hook may be a coroutine; the loader awaits each. Hooks that
    raise are logged + isolated — a faulty plugin cannot crash the
    host. The plugin's own logger receives the full traceback so plugin
    authors can debug their own code.
    """

    # Plugins may declare a Pydantic model describing their settings.
    # When set, the host exposes the plugin's settings in the admin UI
    # and persists changes through :class:`PluginSettingsService`.
    # Stage 12 introduces this; pre-existing plugins (Plex, Sonarr,
    # etc.) don't declare one because their config lives in the
    # Integration model instead.
    settings_schema: type | None = None

    def __init__(self, context: PluginContext) -> None:
        self.context = context

    async def on_load(self) -> None:
        """Called once after registration completes. Lightweight only."""

    async def on_startup(self) -> None:
        """Called once after *all* plugins have loaded.

        Spawn long-lived background tasks here. The loader runs each
        plugin's ``on_startup`` concurrently in a background task and
        does not block the host startup on completion. Any exception is
        logged + swallowed.
        """

    async def on_shutdown(self) -> None:
        """Called once during graceful shutdown, before :meth:`on_unload`.

        Cancel background tasks here so :meth:`on_unload` can run its
        cleanup against a quiesced plugin.
        """

    async def on_unload(self) -> None:
        """Called last, after :meth:`on_shutdown` has returned."""


PluginRegisterFn = "Callable[[PluginContext], Awaitable[Plugin | None] | Plugin | None]"
