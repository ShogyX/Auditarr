---
id: reference/plugins
title: Plugin development
category: reference
tags: [plugins, sdk, extensibility]
summary: Add integrations, widgets, and rule extensions without touching core.
help_context: [help.plugins]
related: [guide/architecture]
---

# Plugin development

A plugin is a directory containing a `manifest.json` and a Python
entrypoint. Drop it under `/app/plugins/` (mounted volume) and restart the
service.

## Manifest

```json
{
  "id": "my-integration",
  "name": "My Integration",
  "version": "0.1.0",
  "type": "integration",
  "backend_entry": "backend.py",
  "routes": true,
  "capabilities": ["media.tags"]
}
```

## Backend entrypoint

```python
from app.plugins import Plugin, PluginContext

class TagProvider:
    def list_tags(self) -> list[str]:
        return ["my-tag"]

def register(context: PluginContext) -> Plugin:
    @context.router.get("/status")
    async def status() -> dict[str, str]:
        return {"ok": "true"}
    context.register_capability("media.tags", TagProvider())
    return Plugin(context)
```

## Discovery and order

Plugins load in dependency order (the manifest's `requires` list is
topologically sorted). Built-in plugins shipped in the image are scanned
first, so a user plugin cannot accidentally shadow a built-in by id.

## What plugins must NOT do

- Import from `app.services.repositories`, `app.storage`, or any other
  internal module not exposed through the SDK.
- Mutate frontend stores or routing.
- Register middleware on the FastAPI app.
- Open database sessions directly.

The SDK is the entire contract. If you find yourself needing more, file a
feature request — it likely belongs in core.
