---
id: dashboard/language-surfaces
title: Language preference surfaces
category: dashboard
tags: [dashboard, languages, audio, subtitles, compatibility]
summary: Two dashboard tiles that flag files needing attention based on language preferences and incompatibility rules.
help_context: [dashboard.foreign-audio, dashboard.incompatible-media]
related: [dashboard/overview, rules/overview]
---

# Language preference surfaces

Two compact dashboard tiles surface files an operator likely wants to act on but might miss in a large library.

## Foreign audio without preferred subtitles

A media file qualifies when:

* Its **primary audio track's language** is NOT in your configured `preferred_audio_languages`, AND
* It carries **no subtitle track** in any of your `preferred_subtitle_languages`.

Operators set the two lists via env var (the defaults are `["eng"]` for both). The tile echoes the active values back so you can see what's configured without leaving the dashboard.

### Tile behavior

* `count > 0` + preferences configured → show the count, the active prefs, and a `View files →` link to `/files?tag=foreign-audio-no-subs`.
* `count == 0` + preferences configured → tile hides entirely. A clean library shouldn't have to stare at a zero.
* Preferences empty (both lists) → tile shows a config nudge with a link to Settings.

### Configuration

Set via env:

```
AUDITARR_PREFERRED_AUDIO_LANGUAGES=eng,fra
AUDITARR_PREFERRED_SUBTITLE_LANGUAGES=eng,fra,spa
```

Both accept comma-separated lists; entries are lowercased on read. ISO 639-2 three-letter codes match what `ffprobe` emits.

### Edge cases

* Files with empty / `und` / `unknown` primary audio are NOT counted. We can't say "this is foreign" without language signal. Operators wanting to triage unknown-language files use the Categories card's "Unknown tracks" section instead.
* Subtitle language matching is case-insensitive and whitespace-trimmed.

## Rule-flagged incompatibilities

This tile counts media files carrying at least one tag whose name contains the substring `incompatible`. Built-in rules use `plex-incompatible-video`, `plex-incompatible-audio`, `jellyfin-incompatible-video`; operator-authored rules with their own `*-incompatible-*` tags (`my-target-incompatible-container`, anything) surface automatically.

### Tile behavior

* `count > 0` → show the count and a `View files →` link to `/files?tag=incompatible` (substring filter).
* `count == 0` → tile hides entirely.

### Wiring incompatibility rules

The tile is "live" only as much as the rules driving it. Operators wanting incompatibility flagging clone one of the built-in templates (under `Rules → Templates`):

* **Plex-incompatible video** — tags files whose video codec isn't in Plex's direct-play set.
* **Plex-incompatible audio** — tags files whose audio codec isn't in Plex's direct-play set.
* **Jellyfin-incompatible video** — same for Jellyfin.

Cloned templates start disabled. Enable, run "Save & Evaluate" against the existing library, and the tile picks up the new tags on the next dashboard refresh.
