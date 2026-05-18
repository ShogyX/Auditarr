---
id: rules/ai-authoring
title: Writing rules with an AI assistant
category: rules
tags: [rules, ai, llm, authoring, import, json]
summary: How to draft Auditarr rules with an AI assistant (ChatGPT, Claude, etc.) and mass-import them via the JSON bundle format.
help_context: [rules.ai-authoring, rules.import]
related: [rules/reference, rules/conditions, rules/actions]
---

# Writing rules with an AI assistant

Auditarr rules are JSON documents. An AI assistant — ChatGPT,
Claude, Gemini, etc. — can draft them for you if you give it
the right context. This page is that context, formatted so you
can paste it into your AI of choice in one block.

## The rule JSON shape

A rule has three top-level keys:

```json
{
 "name": "Human-readable rule name",
 "match": { /* condition tree, see below */ },
 "actions": [ /* one or more actions, see below */ ]
}
```

### `match` — the condition tree

A single condition is `{ "field": "...", "op": "...", "value": ... }`.

Available **fields**:

| Field | Type | Example values |
|---|---|---|
| `video_codec` | string | `"h264"`, `"hevc"`, `"av1"`, `"vp9"`, `"prores"` |
| `audio_codec` | string | `"aac"`, `"ac3"`, `"eac3"`, `"truehd"`, `"dts"` |
| `container` | string | `"mkv"`, `"mp4"`, `"mov"`, `"ts"`, `"avi"` |
| `extension` | string | `"mkv"`, `"mp4"`, `"srt"`, `"nfo"` |
| `width` | int | `1920`, `3840` |
| `height` | int | `1080`, `2160` |
| `bitrate_kbps` | int | `8000`, `25000` |
| `duration_seconds` | int | `5400` |
| `framerate` | float | `23.976`, `29.97`, `60.0` |
| `has_subtitles` | bool | `true`, `false` |
| `subtitle_languages` | string-array | `["en", "es"]` |
| `audio_languages` | string-array | `["en", "ja"]` |
| `category` | string | `"movie"`, `"tv"`, `"music"`, `"other"` |
| `library_id` | string | `"01HXXXXXXX..."` (UUID) |
| `is_orphaned` | bool | `true`, `false` |
| `tags` | string-array | `["plex:1080p", "sonarr:downloaded"]` |
| `path` | string | `"/mnt/media/movies/.../file.mkv"` |
| `filename` | string | `"Movie.Title.2020.1080p.mkv"` |

Available **ops**:

- `eq`, `ne` — equal / not equal. Works for every field.
- `lt`, `lte`, `gt`, `gte` — numeric comparisons. For
 numeric fields only.
- `in`, `not_in` — value is in / not in a provided array.
 For scalar fields; the `value` is an array.
- `contains`, `not_contains` — substring / array-contains.
 For string and string-array fields.
- `matches`, `not_matches` — regex match. For string fields;
 the `value` is a regex pattern.

Combinators — wrap children in `all` (AND) or `any` (OR),
nestable to any depth:

```json
{
 "all": [
 { "field": "video_codec", "op": "eq", "value": "hevc" },
 {
 "any": [
 { "field": "width", "op": "gte", "value": 3840 },
 { "field": "bitrate_kbps", "op": "gte", "value": 25000 }
 ]
 }
 ]
}
```

### `actions` — what to do when the match succeeds

Each action is `{ "type": "...", ...params }`. Available
action types:

- `{ "type": "set_severity", "severity": "ok|info|warn|high|error|crit" }` —
 classify the file's severity.
- `{ "type": "tag", "tag": "string" }` — apply an
 Auditarr-side tag to the file.
- `{ "type": "untag", "tag": "string" }` — remove a tag.
- `{ "type": "delete", "acknowledged_destructive": true }` —
 mark the file for deletion on disk. **Requires the
 `acknowledged_destructive: true` flag** or the engine
 refuses the rule .
- `{ "type": "notify", "channel_id": "string" }` — send a
 notification through a configured channel.

### Optional metadata

```json
{
 "name": "HEVC 4K — flag for review",
 "description": "Optional human description.",
 "enabled": true,
 "priority": 100,
 "match": { ... },
 "actions": [ ... ]
}
```

`priority` (default 100) controls evaluation order when
multiple rules match. Lower = earlier.

## A full example

```json
{
 "name": "Lossless audio that won't direct-play",
 "description": "TrueHD and DTS-HD MA tracks that transcode on most Plex clients.",
 "enabled": true,
 "priority": 50,
 "match": {
 "any": [
 { "field": "audio_codec", "op": "eq", "value": "truehd" },
 { "field": "audio_codec", "op": "eq", "value": "dts-hd ma" },
 { "field": "audio_codec", "op": "matches", "value": "^dts-x$" }
 ]
 },
 "actions": [
 { "type": "set_severity", "severity": "warn" },
 { "type": "tag", "tag": "plex:audio-transcode-likely" }
 ]
}
```

## Drafting a rule with an AI

Paste the block below into your AI assistant. The AI will
return one or more rule JSON documents you can drop into
Auditarr via Settings → Rules → New → "Paste JSON", or via
the **mass-import** flow described below.

> *Hi! I'd like you to help me draft Auditarr rules. Auditarr
> uses a JSON rule format. Here are the fields, operators,
> and actions:*
>
> *— Fields: `video_codec`, `audio_codec`, `container`,
> `extension`, `width`, `height`, `bitrate_kbps`,
> `duration_seconds`, `framerate`, `has_subtitles`,
> `subtitle_languages` (array), `audio_languages` (array),
> `category`, `library_id`, `is_orphaned`, `tags` (array),
> `path`, `filename`.*
>
> *— Ops: `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `in`,
> `not_in`, `contains`, `not_contains`, `matches`,
> `not_matches`.*
>
> *— Actions: `set_severity` (severity:
> `ok|info|warn|high|error|crit`), `tag` (tag: string),
> `untag` (tag: string), `delete`
> (acknowledged_destructive: true required), `notify`
> (channel_id: string).*
>
> *— Combinators: `all` (AND) and `any` (OR), nestable.*
>
> *A rule looks like:*
>
> ```json
> {
> "name": "...",
> "match": { ... },
> "actions": [ ... ]
> }
> ```
>
> *Please draft a rule that does: __<describe what you want
> here>__. Return ONLY the JSON — no commentary — so I can
> paste it directly into Auditarr.*

Replace the italicised "__<describe what you want here>__"
with your actual ask, e.g. *"flags every TV episode in mp4
that's larger than 3 GB and has only English audio"*.

## Mass-importing rules

The Settings → Rules → Import dialog accepts a **JSON array**
of rule documents:

```json
[
 {
 "name": "Rule one",
 "match": { ... },
 "actions": [ ... ]
 },
 {
 "name": "Rule two",
 "match": { ... },
 "actions": [ ... ]
 }
]
```

The importer:

- Validates each rule against the schema. If one rule is
 malformed, the whole import is rejected and an error
 describes which rule failed and why.
- Resolves name collisions according to the `On conflict`
 picker in the dialog: **rename** (the imported rule gets
 a suffix), **replace** (the existing rule is overwritten),
 or **skip** (the imported rule is ignored).
- Returns a summary: how many rules were created, how many
 were renamed/replaced/skipped.

The `acknowledged_destructive: true` flag is enforced on
import — a bundle containing a `delete` action without that
flag is rejected before any rule is written to the database.

## After import: dry-run everything

For each imported rule, click into the rule and use the
**Dry run** button. The preview shows you exactly which
files the rule would flag — without writing any severity or
tag changes. This is especially important when you import
rules from an AI: the AI may have drafted broader conditions
than you intended.

## Related

- [rules/reference](reference) — the formal rule reference.
- [rules/conditions](conditions) — the conditions vocabulary
 in depth.
- [rules/actions](actions) — the actions vocabulary in depth.
- [rules/severity](severity) — the severity values and what
 each one means.
