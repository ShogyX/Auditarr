---
id: dashboard/devices
title: Devices observed
category: dashboard
tags: [dashboard, playback, devices]
summary: Top playback clients ranked by total plays, with per-device transcode ratio.
help_context: [dashboard.devices]
related: [dashboard/overview, integrations/plex, integrations/jellyfin]
---

# Devices observed

The Devices card surfaces which playback clients are hitting the library hardest, and how often each one needs Plex/Jellyfin to transcode for it. It answers two operator questions immediately:

* Which clients keep transcoding? (these are the ones whose codec/container support matters for optimization rules)
* Which clients direct-play everything? (these can be ignored for transcode-rule authoring)

## Data source

The card reads from `GET /api/v1/playback/devices`, which aggregates the `playback_devices` table. That table is populated by the Plex/Jellyfin SSE listener — every playback event that ships with a client identifier upserts a row keyed by a hash of `(client_id, device_name, kind)`.

Each row tracks `playback_count`, `transcode_count`, `direct_play_count`, `direct_stream_count`, `first_seen_at`, and `last_seen_at`. The card shows the top 10 by `playback_count`.

## Per-device row

Each row renders:

* Device name (or `(unnamed device)` if Plex/Jellyfin didn't supply one)
* Platform label
* A horizontal bar showing the percentage of plays that needed transcoding (`transcode_count / playback_count`). The bar uses the warn color so a high-transcode device stands out.
* The transcode percentage as a number on the right.

## Empty state

Until the playback poller has observed any events, the card hides itself entirely (it doesn't render an empty rectangle on the grid). Once the first event ingests, the card appears with one row. There is no manual refresh; the card invalidates on the same cadence as the live-playback card.

## How devices and rules interact

Two rule shapes consume device data:

* **Codec-compatibility rules** that target codecs the operator's heavy-use devices have actually transcoded. The rule recommender (under `Dashboard → Suggestions`) uses device-index data to avoid suggesting "transcode HEVC" if every device direct-plays HEVC.
* **Tag-by-client rules** that author a tag when a specific device touches a file. The matched-rule output references the device name; operators can use that to build per-device exclusion lists.
