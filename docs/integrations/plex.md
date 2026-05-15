---
id: integrations/plex
title: Plex integration
category: integrations
tags: [plex, integrations, media-server]
summary: Connect Plex over the official HTTP API for library and metadata reads.
help_context: [integrations.plex]
related: [integrations/sonarr, integrations/radarr]
---

# Plex integration

Auditarr connects to Plex through the **official HTTP API only**. Authentication
uses an `X-Plex-Token`.

## What works today

- **Healthcheck** — pings `/identity` and reports server name + version.
- **Library discovery** — enumerates `/library/sections` and returns each
  section's name, type (movies / tv / music), and on-disk root path.
  Operators can promote any discovered library to a managed Auditarr library.

## Configuration

Add an integration of kind `plex` in **Settings → Integrations**:

| Field            | Example                          | Notes                          |
|------------------|----------------------------------|--------------------------------|
| Server URL       | `http://plex.local:32400`        | Reachable from the container.  |
| Token            | _your Plex auth token_           | Stored AES-256-GCM encrypted.  |
| Verify TLS       | `true`                           | Set false for self-signed.     |
| Timeout (s)      | `15`                             | Bumped automatically on slow LANs. |

Get a Plex token from a current session by inspecting any Plex web request
for `X-Plex-Token`, or use the official "Get your account token" instructions
in Plex's documentation.

## What's not in this release

- **Optimization endpoints** — Plex's optimization API is incomplete in the
  public surface and the rest is reverse-engineered. That code lives in the
  optimization plugin shipped in a later stage (not this one), so the data
  path stays clean.
- **Tag mirroring** — Plex labels are per-item metadata. We don't sync them
  yet; the provider returns `[]` for `sync_tags`. Adding tag sync later is
  additive and won't affect existing rules.

## How Auditarr authenticates

Every request sets:

```
X-Plex-Token: <your-token>
X-Plex-Client-Identifier: auditarr
X-Plex-Product: Auditarr
X-Plex-Version: <release>
Accept: application/json
```

The token never leaves the server; the frontend has no access to it once
saved.
