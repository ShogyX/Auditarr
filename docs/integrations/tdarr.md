---
id: integrations/tdarr
title: Tdarr integration
category: integrations
tags: [tdarr, integrations, transcoding]
summary: Connect Tdarr for node status and library awareness.
help_context: [integrations.tdarr]
related: [integrations/overview]
---

# Tdarr integration

Tdarr runs the transcode pipeline for many self-hosted setups. Auditarr
reads from Tdarr to surface node availability and to discover the
library roots Tdarr already watches.

## What works today

- **Healthcheck** — `GET /api/v2/status` returns one entry per attached
 Tdarr node. Status flips to `degraded` if any node reports offline.
- **Library discovery** — `POST /api/v2/cruddb` against the
 `LibrarySettingsJSONDB` collection enumerates configured libraries with
 their on-disk roots. Tdarr libraries don't carry a media type, so
 Auditarr reports them as `mixed`; pick the right kind on promote.

## Configuration

| Field | Example |
|------------------|----------------------------------|
| Server URL | `http://tdarr.local:8265` |
| Token (optional) | _bearer token if you set one_ |
| Verify TLS | `true` |
| Timeout (s) | `20` |

Tdarr does not require authentication by default. If you've put it
behind a reverse proxy that adds bearer auth, fill in the **Token**
secret; Auditarr will add `Authorization: Bearer <token>` to every
request.

## What's not in this release

- **Per-file transcode status** as tags. Tdarr's file index is stream
 oriented and huge; mirroring per-file state into Auditarr tags would
 hammer the API. The optimization stage will revisit this
 with a cursor-based pull instead of a full snapshot.
