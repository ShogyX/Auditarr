---
id: integrations/sonarr
title: Sonarr integration
category: integrations
tags: [sonarr, integrations, arr-stack]
summary: Connect Sonarr v3/v4 for TV root folders and per-file tag mirroring.
help_context: [integrations.sonarr]
related: [integrations/radarr, integrations/bazarr, integrations/overview]
---

# Sonarr integration

Auditarr targets Sonarr v3+ (including v4). All endpoints live under
`/api/v3/`. Authentication is via your Sonarr API key — find it under
**Settings → General → API Key**.

## What works today

- **Healthcheck** — `GET /api/v3/system/status`. Returns the Sonarr
  instance name, version, branch.
- **Library discovery** — `GET /api/v3/rootfolder` enumerates Sonarr's
  configured root folders. Each becomes a candidate Auditarr library of
  kind `tv`.
- **Tag mirroring** — `GET /api/v3/series` + `GET /api/v3/tag` run in
  parallel. Every series-level tag produces one `TagSync` keyed by the
  series' on-disk path. The rules engine later joins these with media
  files under that path.

## Configuration

| Field                   | Example                          |
|-------------------------|----------------------------------|
| Server URL              | `http://sonarr.local:8989`       |
| API key                 | _from Settings → General_        |
| Verify TLS              | `true`                           |
| Timeout (s)             | `15`                             |
| Mirror tags per file    | `true`                           |

Disable **Mirror tags per file** if your Sonarr has thousands of series
with many tags and you'd rather not bring all of that into Auditarr.

## How tag rows look

Sonarr series:

```
Show A → tags [1, 2]   (path /data/tv/Show A)
Show B → tags [1]      (path /data/tv/Show B)
Show C → tags []       (no tag rows emitted)
```

Sonarr tag dictionary:

```
1 → "4k"
2 → "anime"
```

Produces these `TagSync` rows (one per (path, tag) pair):

```
(/data/tv/Show A, 4k)
(/data/tv/Show A, anime)
(/data/tv/Show B, 4k)
```
