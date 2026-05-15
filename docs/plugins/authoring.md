---
id: plugins/authoring
title: Authoring plugins
category: plugins
tags: [plugins, sdk, authoring, lifecycle, settings]
summary: Build your own Auditarr plugin — lifecycle, capabilities, settings, scaffolder.
help_context: [plugins.authoring]
related: [integrations/overview, notifications/overview]
---

# Authoring plugins

Plugins extend Auditarr without forking the codebase. A plugin is a
directory on disk containing a `manifest.json` and a Python entry point.
At startup the loader discovers each plugin, calls its `register()`
function, and from then on the plugin participates in the lifecycle
alongside the host.

This page is the SDK reference. For end-user docs (browsing what's
installed, the gallery, configuring an installed plugin) see the
**Settings → Plugins** section in the app.

## Quickstart

```bash
# From inside the Auditarr container (or anywhere with the source tree):
auditarr plugin-new my-thing --target-dir ./plugins
```

You get a working skeleton:

```
plugins/my-thing/
├── manifest.json
├── __init__.py
├── README.md
└── tests/
    ├── __init__.py
    └── test_plugin.py
```

Restart Auditarr. The plugin shows up under **Settings → Plugins**.

## Anatomy of a plugin

### `manifest.json`

```json
{
  "id": "my-thing",
  "name": "My Thing",
  "version": "0.1.0",
  "type": "generic",
  "description": "Does the thing.",
  "backend_entry": "__init__.py",
  "capabilities": []
}
```

The `id` field must be lowercase letters, digits, and dashes, starting
with a letter, between 2 and 48 characters. Other plugins can collide
with it only by replacing its directory — built-in plugins are loaded
first so user-supplied plugins can never accidentally shadow them.

`type` is one of:

| Value | Purpose |
|-------|---------|
| `integration` | Connector to an external service (Plex, Sonarr, etc.) |
| `notification` | Channel for rule alerts |
| `optimization` | Custom transcode profile or worker |
| `rule` | Custom rule action or operator |
| `widget` | Dashboard widget |
| `docs` | Pure documentation plugin |
| `generic` | Anything else |

### `__init__.py`

The entry point must define a top-level `register(context)` callable.
It can be sync or async, and can return either `None` (the plugin is
load-only) or a `Plugin` subclass instance (to participate in the
lifecycle).

```python
from app.plugins.contracts import Plugin, PluginContext

class MyPlugin(Plugin):
    async def on_startup(self):
        log = self.context.logger()
        log.info("my_thing.started")

async def register(context: PluginContext) -> MyPlugin:
    return MyPlugin(context)
```

## The plugin context

The argument to `register()` is the SDK surface:

| Method | Purpose |
|--------|---------|
| `context.manifest` | The parsed `manifest.json` |
| `context.directory` | The plugin's directory on disk |
| `context.register_capability(name, provider)` | Register an arbitrary capability |
| `context.register_integration(provider)` | Register an integration (Stage 5 SDK) |
| `context.register_notification_channel(provider)` | Register a channel (Stage 9 SDK) |
| `context.http_client(base_url=, timeout=, headers=)` | Pre-configured `httpx.AsyncClient` |
| `context.events` | The shared `EventBus` |
| `context.logger()` | Structured logger bound to the plugin id |

Capabilities registered via these methods become visible to the host
immediately — no restart required for the *register* call itself; the
restart is only needed to pick up code changes inside the plugin.

## Lifecycle

A `Plugin` subclass has four hooks. The loader fires them in order, all
of them isolated — a hook that raises does not crash the host and does
not propagate to other plugins:

| Hook | Fires when | Use for |
|------|------------|---------|
| `on_load` | Immediately after `register()` returns | Lightweight setup. Synchronous host startup will await this. |
| `on_startup` | After *every* plugin has loaded and the host is otherwise ready | Long-lived background tasks. Spawned as an asyncio task; host won't block on it. |
| `on_shutdown` | At graceful shutdown, before `on_unload` | Cancel background tasks. |
| `on_unload` | Last, after `on_shutdown` returns | Release resources. |

If `on_load` raises, the plugin is marked as lifecycle-failed and
subsequent hooks are skipped — its `register()` registrations are kept
(other code may depend on them), but the lifecycle itself is short-
circuited so cascading errors don't accumulate.

If `on_startup` raises, the failure is logged, a `plugin.error` event
is emitted on the bus, and other plugins are unaffected.

## Plugin settings

Declare a Pydantic model as your `Plugin.settings_schema`:

```python
from pydantic import BaseModel, Field

class MyThingSettings(BaseModel):
    enabled: bool = Field(default=True)
    polling_interval_seconds: int = Field(default=300, ge=30, le=86400)
    target_url: str | None = None

class MyPlugin(Plugin):
    settings_schema = MyThingSettings
```

The host exposes the schema in the UI (**Settings → Plugins → My
Thing**) as a JSON-edited form pre-populated with the defaults from
your model. Writes are validated against the schema; out-of-range
values are rejected with a 422 from the API.

Read persisted values from your plugin code:

```python
from app.services.plugin_settings import PluginSettingsService

async def my_periodic_task(session):
    settings = await PluginSettingsService(session).values_or_defaults(
        "my-thing"
    )
    if not settings["enabled"]:
        return
    interval = settings["polling_interval_seconds"]
    ...
```

`values_or_defaults` merges the persisted values on top of the schema's
defaults, so missing keys never raise.

### What plugin settings are NOT for

- **Secrets.** Plugin settings are stored unencrypted. If you need API
  keys or passwords, register an *integration* instead — the
  `Integration` model carries AES-256-GCM-encrypted secrets and the SDK
  handles the encryption transparently.
- **Per-resource config.** Settings are per-plugin (one row), not
  per-thing-the-plugin-manages. A plugin that polls N servers should
  expose each server as an integration, not pack a list into settings.

## Capabilities

Beyond the dedicated `register_integration` / `register_notification_channel`
SDK methods, anything else goes through `register_capability(name, provider)`.
The host exposes registered capabilities at `/api/v1/system/capabilities`
and the registry lookup is namespace-flat — plugins should prefix names
to avoid collision (e.g. `widget.my-thing.summary`).

A capability is just an arbitrary Python object the host stashes by
name. The contract between plugin and consumer (some other piece of host
code, or another plugin) is whatever you both agree on.

## Logging

`context.logger()` returns a `structlog`-compatible logger pre-bound to
your plugin's id and version. Use it instead of `print()` or stdlib
`logging` — the host's log pipeline filters by category and structures
output for the dashboard.

```python
log = self.context.logger()
log.info("fingerprint.complete", path=p, duration_ms=elapsed)
log.warning("fingerprint.skipped", reason="too short")
```

## Network calls

`context.http_client(base_url=, timeout=, headers=)` gives you an
`httpx.AsyncClient` with sensible defaults (15s timeout, follow
redirects). Use it inside `async with` so connection pools clean up
correctly:

```python
async with self.context.http_client(base_url="https://api.example.test") as client:
    response = await client.get("/things")
```

The host adds no auth/credentials automatically. Pull them from plugin
settings or the integration secrets layer as appropriate.

## Events

`context.events` is the shared `EventBus`. Plugins can both publish and
subscribe.

Canonical event names live in `app.events.types.EVENT_NAMES`. Plugins
may emit names from that list, or namespace their own
(`plugin.my-thing.detected`).

```python
async def on_startup(self):
    self.context.events.subscribe("scan.completed", self._on_scan_done)

async def _on_scan_done(self, event):
    log = self.context.logger()
    log.info("my_thing.saw_scan", run_id=event.payload.get("run_id"))
```

## Testing your plugin

The scaffolder generates a working test that imports the plugin module
by path (the same way the loader does), instantiates `PluginContext`
with mocked dependencies, and calls `register()`. Run it with `pytest`
against your local Auditarr backend checkout:

```bash
PYTHONPATH=/path/to/auditarr/backend pytest plugins/my-thing/tests
```

For deeper tests, follow the pattern in `tests/integration/test_plugin_lifecycle.py`
— write plugin source to a temp directory, point a `PluginLoader` at it,
and assert on the lifecycle behaviour you care about.

## Publishing

The gallery is just a JSON manifest at
`AUDITARR_PLUGIN_GALLERY_URL` (the default points at the project's
community gallery on GitHub). To list a plugin there, open a PR
adding an entry like:

```json
{
  "id": "my-thing",
  "name": "My Thing",
  "description": "Does the thing.",
  "author": "@you",
  "version": "0.1.0",
  "source_url": "https://github.com/you/my-thing",
  "install_url": "https://github.com/you/my-thing/releases/download/v0.1.0/my-thing-0.1.0.tar.gz",
  "install_instructions": "Extract into ./plugins/ and restart Auditarr.",
  "categories": ["analysis"]
}
```

The gallery is a **directory**, not an auto-installer. Operators see
your entry and either copy the install URL or follow your instructions
manually — Auditarr never reaches out and runs unsigned tarballs on the
operator's behalf.

## What's NOT in the SDK

- **In-process hot reload.** Reloading a plugin's Python code without
  restarting the host is technically possible and a footgun in
  practice — long-running asyncio tasks, stale capability references,
  and orphaned event subscribers all conspire to make it unreliable.
  Restart the host (or the container) after changing plugin code.
- **Auto-install from the gallery.** See above. Trust boundary lives
  with the operator.
- **Cross-plugin direct imports.** Plugins should not `import` other
  plugins. Communicate through capabilities or events. The plugin
  loader does not guarantee load order, so import-time coupling is a
  bug waiting to happen.
