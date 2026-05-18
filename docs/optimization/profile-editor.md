---
id: optimization/profile-editor
title: Optimization profile editor
category: optimization
tags: [optimization, profiles, encoding, ffmpeg]
summary: Structured editor for optimization profiles — no FFmpeg flags by hand.
help_context: [optimization.profiles, optimization.profile.editor]
related: [optimization/overview]
---

# Optimization profile editor

Auditarr's optimization profiles describe an encoding target as a
**structured profile** rather than raw FFmpeg flags. The editor
dialog renders one labeled input per option so you don't have to
memorize the FFmpeg command line.

## What a profile contains

| Field | Purpose | Example |
|---|---|---|
| **Name** | Profile identifier referenced by rules and the queue UI. | `h265-medium` |
| **Description** | Free-form note for the operator. | `H.265 medium-quality reencode for archive` |
| **Video codec** | The target codec. Enum from FFmpeg's supported encoders. | `libx265` |
| **Audio codec** | The target audio codec, or `copy` to passthrough. | `copy` |
| **Container** | Target output container. | `mkv` |
| **CRF / Bitrate** | Constant Rate Factor for codecs that support it; bitrate fallback for codecs that don't. | `CRF 23` |
| **Preset** | Encoder speed/quality preset. | `medium` |
| **Hardware acceleration** | When supported, picks a hwaccel device. | `vaapi` |
| **Integration routing** | Which integration (Plex, Jellyfin, etc.) consumes the output. | `plex` |
| **Notes** | Free-form text shown on the job-run row. | (optional) |

The editor groups these into sections (Codecs / Quality / Container /
Routing / Notes) for readability. Required fields are marked with an
asterisk; optional fields show a hint inline.

## Live preview

The bottom of the dialog has a read-only **command preview** showing
the FFmpeg invocation the worker will build from your profile. This
is a diagnostic — it updates as you edit, and is purely for verifying
the shape without leaving the dialog. You can't edit it; the
structured fields are the source of truth.

## changes

- Routing column was made **explicitly nullable** in the schema —
 patching `integration_routing: null` now correctly clears the
 field instead of silently dropping the value.
- Required-fields check runs client-side before submit so a misconfigured
 profile surfaces an inline error before the round-trip.

## See also

- [Optimization overview](/help/optimization/overview) — the broader
 scheduling + queue model.
- [Rule actions](/help/rules/actions) — how `queue_optimization`
 references a profile by name.
