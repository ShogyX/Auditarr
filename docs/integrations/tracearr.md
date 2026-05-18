---
id: integrations/tracearr
title: Tracearr
category: integrations
tags: [integrations, tracearr, playback, telemetry]
summary: Receive playback telemetry from Tracearr's lightweight collector.
help_context: [integrations.tracearr]
related: [integrations/plex, integrations/jellyfin, dashboard/devices]
---

# Tracearr

Tracearr is a lightweight playback-telemetry collector that hands events off to Auditarr. Use it when your media server doesn't expose a usable playback API (some self-hosted setups, restricted Jellyfin builds) or when you want to capture playback signals from a downstream proxy without relying on the server's own session endpoints.

## What Tracearr ships

The Tracearr → Auditarr handoff is a sequence of HTTP POSTs to `/api/v1/integrations/tracearr/events`. Each event carries:

* `event_kind` — `play_start`, `play_progress`, `play_stop`, or `transcode_decision`.
* `media_identifier` — a path or hash that Auditarr resolves to a `MediaFile` row through the integration's `path_mappings`.
* Device + user fields (client identifier, name, platform).
* `decision` — the chosen play mode (`direct_play`, `direct_stream`, `transcode`) when known.
* `source_codec`, `target_codec`, `source_bitrate_kbps`, `target_bitrate_kbps` when the event is `transcode_decision`.

Auditarr converts the event into a `PlaybackEvent` row with `provider="tracearr"`. The same downstream analyzer that processes Plex/Jellyfin events processes Tracearr events — no separate code path.

## Configuration

Configure under `Integrations → New connector → Tracearr`. The form asks for:

* **Base URL** — where Tracearr is reachable from Auditarr's host. Used for the healthcheck path probe (`/health`, `/api/health`, `/api/v1/health`, `/status` — Auditarr tries them in order so a Tracearr build with any conventional health endpoint works).
* **API key** — the secret Tracearr sends in `Authorization: Bearer <key>` headers. Stored encrypted at rest.
* **Path mappings** — list of `(src_prefix, dst_prefix)` rewrites. Tracearr typically sees the same paths the media server does; if your library is mounted differently in Tracearr than in Auditarr, define mappings here so `media_identifier` resolves correctly.

## Auth on the inbound side

The `/integrations/tracearr/events` endpoint validates the `Authorization` header against the integration's stored key. A missing or wrong key returns 401; a payload from an unconfigured Tracearr returns 404 (the integration row keyed by `kind=tracearr` is the auth principal).

## Healthcheck behavior

The integration's healthcheck (manual refresh button or scheduled) makes a GET to each of the candidate paths in order. The first 200 response wins; if none responds 200, the integration goes into the `error` health state with the last attempted URL recorded for diagnostics.
