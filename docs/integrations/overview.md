---
id: integrations/overview
title: Integrations overview
category: integrations
tags: [integrations, connectors, plugins]
summary: How Auditarr connects to upstream services, where secrets live, and how to add a new connector.
help_context: [integrations.overview]
related: [integrations/plex, reference/plugins]
---

# Integrations overview

Each upstream service Auditarr talks to (Plex, Sonarr, Radarr, Bazarr,
Tdarr, …) is implemented as a **built-in plugin** that registers an
`IntegrationProvider`. The integration manager dispatches healthcheck,
library discovery, and tag sync calls through that provider.

## What you configure

For each integration you create one row in **Settings → Integrations**:

- A unique **name** (your label, e.g. `Plex Home`).
- The **kind** (which provider to use — `plex`, `sonarr`, etc.).
- Public **config** — base URL, polling interval, library hints.
- **Secrets** — API tokens, passwords. Stored AES-256-GCM encrypted at
  rest, never returned in any API response.

## How secrets are protected

- Encrypted with AES-256-GCM. The key is derived from
  `AUDITARR_SECRET_KEY` via HKDF-SHA256 with a domain-separation context.
- Stored as a versioned base64 blob in the `integrations` table.
- Rotating `AUDITARR_SECRET_KEY` invalidates all stored secrets; operators
  re-enter them. This is intentional — recovery without the key is the
  whole point.
- Decryption only happens on the server; the frontend never sees a token.

## Healthchecks

Every enabled integration is polled at `poll_interval_seconds` (default
300s). The most recent `health_status` and `health_detail` are persisted on
the integration row and shown on the dashboard. A `integration.health_changed`
event fires when the status flips, so rules and notifications can react.

## Reachability is verified up front

Auditarr never saves an integration it can't actually reach. When you
click **Connect** (or `POST /api/v1/integrations`):

1. The provider's `healthcheck` runs against the candidate config first,
   using the URL and secrets you just typed.
2. If the upstream responds, the row is saved and a follow-up healthcheck
   persists the success state immediately — the dashboard never shows
   "unknown" for a working integration.
3. If the upstream rejects the credentials or is unreachable, the request
   fails with HTTP 422 and the detail from the provider (e.g. "API key
   rejected", "connection refused"), and nothing is saved.

Use the **Test** button in the Connect dialog (or `POST /api/v1/integrations/test`)
to preflight without committing.

Updates that change the config or secrets re-run the preflight too —
swapping a bad URL into a working integration will be rejected with the
old config still in place.

For the rare case where you need to register an integration whose
upstream is intentionally unavailable (coordinated maintenance, deploying
ahead of the service it talks to), the API accepts
`?skip_preflight=true`. The frontend doesn't expose this; it's an
escape hatch for operators.

## Sync operations re-verify before running

`discover_libraries` and `sync_tags` re-run the healthcheck immediately
before contacting the upstream. A rotated token or downed server can't
silently turn into a stream of confusing tag-sync failures — the manager
raises before the broken call lands and updates the persisted health
state as a side effect.

## Adding a new connector

A connector is just a plugin. The minimum viable example:

```python
from app.integrations import (
    DiscoveredLibrary,
    HealthReport,
    IntegrationConfig,
    IntegrationProvider,
)
from app.plugins import Plugin, PluginContext

class MyProvider(IntegrationProvider):
    kind = "my-service"
    label = "My Service"
    config_schema = {
        "type": "object",
        "required": ["base_url"],
        "properties": {"base_url": {"type": "string"}},
    }
    secret_fields = ("api_key",)

    async def healthcheck(self, config: IntegrationConfig) -> HealthReport:
        ...

    async def discover_libraries(self, config: IntegrationConfig) -> list[DiscoveredLibrary]:
        return []

    async def sync_tags(self, config: IntegrationConfig):
        return []

def register(ctx: PluginContext) -> Plugin:
    ctx.register_integration(MyProvider())
    return Plugin(ctx)
```

The `manifest.json` declares `"capabilities": ["integration.my-service"]`
and the plugin's id must match `provider.kind`. Drop the directory under
`/app/plugins/` and restart; the integration manager picks it up
automatically.
