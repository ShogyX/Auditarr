---
id: integrations/radarr
title: Radarr integration
category: integrations
tags: [radarr, integrations, arr-stack]
summary: Connect Radarr v3+ for movie root folders and per-file tag mirroring.
help_context: [integrations.radarr]
related: [integrations/sonarr, integrations/bazarr, integrations/overview]
---

# Radarr integration

Same shape as the Sonarr connector — the *arr-stack APIs are siblings.
Auditarr targets Radarr v3+ and consumes `/api/v3/`. Authentication is
via your Radarr API key under **Settings → General → API Key**.

## What works today

- **Healthcheck** — `GET /api/v3/system/status` returns instance name and
  version.
- **Library discovery** — `GET /api/v3/rootfolder` enumerates Radarr's
  root folders. Each becomes a candidate Auditarr library of kind
  `movies`.
- **Tag mirroring** — `GET /api/v3/movie` + `GET /api/v3/tag` resolve
  every movie-level tag into a `TagSync` row keyed by the movie's
  on-disk path.

## Configuration

| Field                   | Example                          |
|-------------------------|----------------------------------|
| Server URL              | `http://radarr.local:7878`       |
| API key                 | _from Settings → General_        |
| Verify TLS              | `true`                           |
| Timeout (s)             | `15`                             |
| Mirror tags per file    | `true`                           |

See the [Sonarr integration](#sonarr) docs for an example of how tag
rows are expanded — the shape is identical.
