---
id: integrations/tracearr
title: Tracearr
category: integrations
tags: [integrations, tracearr, playback, telemetry]
summary: Pull playback history from a Tracearr instance via its read-only public API.
help_context: [integrations.tracearr]
related: [integrations/plex, integrations/jellyfin, dashboard/devices]
---

# Tracearr

[Tracearr](https://github.com/connorgallopo/Tracearr) is a streaming-access manager for Plex, Jellyfin, and Emby. It maintains its own session-history database and exposes a read-only public API for third-party integrations. This connector polls that API and persists each play into Auditarr's `playback_events` table — the same downstream analyser that processes Plex and Jellyfin events handles Tracearr events.

Use this when you already run Tracearr and want a single source of truth for "every play, across every server" without standing up parallel pollers per upstream.

## Configuration

Configure under **Integrations → New connector → Tracearr**. The form asks for:

* **Base URL** — Tracearr's HTTP base (e.g. `http://tracearr:3000`). Do **not** include `/api`; the connector appends `/api/v1/public` itself.
* **API key** — generated in Tracearr under **Settings → General**. Tokens are of the form `trr_pub_<base64url>` and are sent as `Authorization: Bearer <token>`. Stored encrypted at rest in Auditarr.
* **Page size** *(optional, default 100, max 100)* — how many history rows to request per `/history` page. Tracearr caps the value server-side; the connector clamps client-side so a misconfigured value falls back gracefully.

There are no path mappings on this integration. Tracearr does not surface downstream file paths, so Auditarr synthesises a stable `tracearr://<serverId>/<mediaType>/<title>...` pseudo-path for each event. The `media_file_id` foreign key remains NULL — Tracearr rows live alongside Plex/Jellyfin rows in `playback_events` but don't join to local `media_files`.

## What gets ingested

For every Tracearr session the connector ingests:

* `upstream_id` ← Tracearr's `id` (a play UUID grouped by `reference_id`, stable across the play's lifetime).
* `started_at` / `completed_at` ← `startedAt` / `stoppedAt`.
* `duration_s` ← `durationMs / 1000`.
* `decision` ← derived from `videoDecision` / `audioDecision` / `isTranscode`:
  * `transcode` if either track was re-encoded **or** `isTranscode` is true.
  * `direct_stream` if at least one track was remuxed (`copy`).
  * `direct_play` otherwise.
* `device_kind` ← `platform`; `device_name` ← `player` (fallback `product` → `device`).
* `source_codec` / `source_width` / `source_height` / `source_bitrate_kbps` — from `sourceVideo*` fields and `sourceVideoDetails.bitrate`.
* `target_codec` / `target_bitrate_kbps` — from `streamVideo*` and `streamVideoDetails.bitrate` when the row represents a transcode.
* `reason_code` ← `transcodeInfo.reasons` joined with `,` (e.g. `video.codec.unsupported,bitrate.cap`).

`failed` plays are not exposed in Tracearr's history (Tracearr stores only sessions that entered its `sessions` table), so this connector never emits `decision="failed"`.

## Polling cadence

The worker's `poll_playback` cron tick (15 min by default) iterates every enabled playback integration. Tracearr is filtered through the same `PLAYBACK_KINDS` whitelist as Plex / Jellyfin (`backend/app/worker.py`). Each tick:

1. Reads the per-integration cursor from `integration_cursors`.
2. Calls `GET /api/v1/public/history?page=1&pageSize=N&timezone=UTC&startDate=<cursor>`.
3. Walks subsequent pages until `page * pageSize ≥ meta.total`, capped at 50 iterations per tick as a misbehaviour guard (≈5000 events with `pageSize=100`).
4. Maps each row, runs the deduplication step (`(integration_id, upstream_id)` unique constraint), and commits.

Tracearr's history endpoint filters by *date* rather than timestamp, so each tick re-fetches the day containing the cursor. The unique constraint dedupes the overlap silently.

## Healthcheck behaviour

The integration's healthcheck (manual refresh button or scheduled) walks the following endpoints in order until one responds:

1. `/health` — Tracearr's unauthenticated probe; returns `{"status":"ok","db":true,…}`.
2. `/api/v1/public/health` — same content gated by Bearer auth. A 401 here surfaces as `degraded` with a hint pointing operators at **Settings → General** and the `trr_pub_` prefix.
3. `/api/health`, `/api/v1/health`, `/status` — legacy fallbacks for unusual proxy setups.

The first non-404 wins; an HTTP 5xx or network error sets `error`, a non-`ok` status payload (`"degraded"` / `"unhealthy"`) sets `degraded`.

## Operational notes

* Tracearr's `reference_id` grouping means a single play with multiple state transitions (pause → resume → stop) surfaces as one row, not three. Auditarr ingests it once and trusts Tracearr's grouping.
* Path mappings configured globally in Auditarr are not applied to synthesised `tracearr://` paths — there's nothing meaningful to remap.
* Rule-engine actions that resolve through `media_file_id` will skip Tracearr rows; rules keyed on `device`, `decision`, `user`, or codec fields work normally.
