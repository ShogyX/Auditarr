---
id: dashboard/categories
title: Categories card
category: dashboard
tags: [dashboard, categories, composition]
summary: Library composition at a glance — resolutions, languages, containers, and median bitrate.
help_context: [dashboard.categories]
related: [dashboard/overview, files/overview]
---

# Categories card

The Categories card answers the questions operators ask about their library's shape: how much is 4K, what audio languages are present, which codec/container combinations weigh the most.

## Sections

The card is one API call (`GET /api/v1/dashboard/composition`) that returns a structured payload, and the card renders one section per kind:

1. **Resolutions** — counts per bucket (`<480p`, `480p`, `720p`, `1080p`, `1440p`, `4K`, `8K`, `Unknown`). Buckets are computed from probed `height`; files with no probed height land in `Unknown`.
2. **Top extensions** — top 8 by file count. Click-through links to `/files?extension=<key>` for drill-in.
3. **Containers** — normalized labels (MKV, MP4, WEBM, …) collapsed across raw ffprobe demuxer aliases.
4. **Subtitle formats** — SRT, ASS, PGS, VobSub, etc. from probed subtitle codecs.
5. **Subtitle languages** — top 8.
6. **Audio languages** — top 8.
7. **Unknown tracks** — count of files that probed successfully but came back with NULL `video_codec` or NULL `audio_codec`. A probe-stage health signal.
8. **Internal vs external subtitles** — embedded stream count vs sidecar `.srt`/`.ass` file count.
9. **Orphan files** — count of media files marked `is_orphaned` (file missing on disk since last scan).
10. **Median bitrate** — a sortable matrix of `(library, resolution, codec, container)` cells, each with a file count and a median bitrate.

## Median bitrate matrix

Each row shows both **Mbps** (primary, friendly) and **kbps** (muted secondary, accurate). Click any column header to sort by that key. Numeric columns default to descending on the first click (highest first is usually what you want); string columns default to ascending. Click the same column twice to flip direction.

Each row is a deep-link to the Files page filtered by codec and container — the click navigates straight to the matching rows so you can act on the bucket.

## Empty state

A fresh install before its first scan shows an empty state with a "Add a library and run a scan" nudge. The composition payload doesn't compute anything when `media_files` is empty — the response is structurally complete (every section returns an empty list) so the client renders the empty state without an error path.
