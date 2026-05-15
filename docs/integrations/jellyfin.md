---
id: integrations/jellyfin
title: Jellyfin integration
category: integrations
tags: [jellyfin, integrations, media-server]
summary: Connect Jellyfin over the official HTTP API for library reads.
help_context: [integrations.jellyfin]
related: [integrations/plex, integrations/overview]
---

# Jellyfin integration

Targets Jellyfin 10.8+. Authentication is via an API key from Jellyfin's
**Dashboard → API Keys** screen. Auditarr sends it as `X-Emby-Token` (the
header Jellyfin inherited from its Emby roots and still accepts).

## What works today

- **Healthcheck** — `GET /System/Info` returns the server name, version,
  OS, and unique id. A 401 response surfaces as "API key rejected".
- **Library discovery** — `GET /Library/VirtualFolders` enumerates each
  configured library and its on-disk roots. Jellyfin libraries can list
  multiple physical paths under one name; we emit one
  `DiscoveredLibrary` per location so you can pick exactly which paths
  Auditarr should scan.

## Configuration

| Field            | Example                          |
|------------------|----------------------------------|
| Server URL       | `http://jellyfin.local:8096`     |
| API key          | _from Dashboard → API Keys_      |
| Verify TLS       | `true`                           |
| Timeout (s)      | `15`                             |

## What's not in this release

- **Tag mirroring** — Jellyfin tags are per-item metadata; mirroring would
  require paginating `/Items?fields=Tags,Path` across thousands of rows.
  Will land alongside Stage 8 (Dashboard & Analytics) when we have a
  cursor strategy that doesn't blow up the upstream API.

## Collection type → Auditarr kind

| Jellyfin `CollectionType` | Auditarr kind |
|---------------------------|---------------|
| `movies`, `boxsets`       | `movies`      |
| `tvshows`                 | `tv`          |
| `music`, `musicvideos`    | `music`       |
| `homevideos`              | `mixed`       |
| _anything else / null_    | `mixed`       |
