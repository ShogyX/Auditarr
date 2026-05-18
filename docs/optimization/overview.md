---
id: optimization/overview
title: Optimization
category: optimization
tags: [optimization, transcoding, ffmpeg, profiles]
summary: Profiles, the queue, and the worker that runs ffmpeg.
help_context: [optimization.overview]
related: [rules/reference, automation/overview]
---

# Optimization

A **profile** is a named transcoding preset (codec, container, audio
handling, scale). Rules with a `queue_optimization` action enqueue
items referencing a profile by name. The **worker** picks the oldest
queued item every minute, runs ffmpeg, validates the output, and
atomically swaps it into place.

## Profile shape

```json
{
 "video": {
 "codec": "libx265",
 "crf": 22,
 "preset": "medium",
 "max_bitrate_kbps": null,
 "scale_height": null
 },
 "audio": { "codec": "copy", "bitrate_kbps": 128, "channels": null },
 "subtitles": { "handling": "copy" },
 "output": {
 "container": "mkv",
 "replace_input": true,
 "keep_backup": true
 },
 "extra_args": [],
 "skip_if_bitrate_below_kbps": null
}
```

### Supported values

| Field | Allowed |
|-------|---------|
| `video.codec` | `libx265`, `libx264`, `libaom-av1`, `copy` |
| `video.preset` | `ultrafast`…`veryslow` |
| `audio.codec` | `libopus`, `aac`, `libmp3lame`, `copy` |
| `subtitles.handling` | `copy`, `drop` |
| `output.container` | `mkv`, `mp4`, `webm` |

`extra_args` is a free-form list of ffmpeg arguments inserted just
before the output path. Use sparingly — there's no validation.

## How the worker decides what to do

For each item it claims, the worker:

1. Loads the referenced profile; refuses if missing or disabled.
2. Confirms the input file still exists on disk.
3. Checks the input is under `max_input_bytes` (on the profile, if set).
4. Checks the input's bitrate is above `skip_if_bitrate_below_kbps`
 (on the profile, if set) — otherwise marks the item `skipped`.
5. Runs ffmpeg writing to `<input>.auditarr.tmp.<ext>`.
6. Runs ffprobe on the temp output — must have a video stream and
 duration within ±2% of the input.
7. Atomically swaps:
 - If `keep_backup` is true: renames original to `<input>.bak`.
 - Otherwise: deletes the original.
 - Then `os.replace`s the temp output to `<input stem>.<new ext>`.

Failure at any step leaves the original untouched and writes the error
detail to the item row.

## Progress

The runner parses `out_time_us=` events from ffmpeg's `-progress pipe:1`
output and divides by the input's known duration to compute a percent.
Every change is persisted to `optimization_items.progress_pct` and
emitted as an `optimization.progress` event on the bus. The UI
re-queries the queue every 5 seconds while items are active.

## Queue states

```
queued → running → completed
 ↘ → failed
 ↘ → cancelled
queued ───→ skipped (precondition failed)
```

- **queued** — waiting for the next worker tick.
- **running** — ffmpeg is in flight; `progress_pct` ticks up.
- **completed** — output validated and swapped (or saved alongside).
- **failed** — ffmpeg returned non-zero, validation failed, or the swap
 raised. `error` carries the detail.
- **cancelled** — operator clicked cancel; running items are best-effort
 signalled to terminate.
- **skipped** — preconditions (size, bitrate, disabled profile) were
 not met.

Failed/cancelled/skipped items can be **retried** from the UI, which
resets them back to `queued`.

## Endpoints

| Path | Purpose |
|------|---------|
| `GET /api/v1/optimization/profiles` | List profiles |
| `POST /api/v1/optimization/profiles` | Create (admin) |
| `PATCH /api/v1/optimization/profiles/{id}` | Update (admin) |
| `DELETE /api/v1/optimization/profiles/{id}` | Delete (admin) |
| `GET /api/v1/optimization/queue` | List queue items; filter by `status` |
| `POST /api/v1/optimization/enqueue` | Manually enqueue (admin) |
| `POST /api/v1/optimization/run-next` | Run the oldest queued item (admin) |
| `GET /api/v1/optimization/{id}` | Get one item |
| `POST /api/v1/optimization/{id}/run` | Run a specific item (admin) |
| `POST /api/v1/optimization/{id}/cancel` | Cancel (admin) |
| `POST /api/v1/optimization/{id}/retry` | Re-queue a failed/cancelled item (admin) |

## Worker scheduling

The worker runs as a cron tick named `optimization_tick` inside the ARQ
worker, alongside `poll_integrations` and `automation_tick`. It picks at
most one queued item per minute. Run the worker container with
`docker compose --profile worker up -d`; without it, items sit in the
queue until you click **Run next** or **Run** on a specific item.

## What's deferred to polish

- Parallel workers — currently one-at-a-time, which is what most
 self-hosted boxes want anyway given CPU contention.
- Hardware acceleration (VAAPI/NVENC) — profiles can be hand-coerced
 into using these via `extra_args`, but the schema doesn't yet model
 device selection.
- Detection of "no useful work to do" — a re-transcode of an already-x265
 file at the same CRF will produce a similar-sized output. may
 add a "only run if expected savings > X%" pre-flight.
