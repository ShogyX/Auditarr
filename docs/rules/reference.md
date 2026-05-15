---
id: rules/reference
title: Rule reference
category: rules
tags: [rules, dsl, conditions, actions]
summary: The complete rule DSL — fields, operators, and actions.
help_context: [rules.conditions, rules.actions]
related: [rules/conditions, rules/actions, rules/severity]
---

# Rule reference

A rule is a JSON document with two parts: a **match** tree (the
conditions) and an **actions** list (what to apply when the match
succeeds).

## Minimal rule

```json
{
  "match": { "field": "video_codec", "op": "eq", "value": "hevc" },
  "actions": [{ "type": "set_severity", "severity": "warn" }]
}
```

## Combinators

Conditions combine with `all` (AND) or `any` (OR), nestable:

```json
{
  "match": {
    "any": [
      {
        "all": [
          { "field": "container", "op": "eq", "value": "mkv" },
          { "field": "has_subtitles", "op": "eq", "value": false }
        ]
      },
      { "field": "is_orphaned", "op": "eq", "value": true }
    ]
  },
  "actions": [{ "type": "set_severity", "severity": "high" }]
}
```

## Supported fields

| Field                | Type    | Examples                       |
|----------------------|---------|--------------------------------|
| `filename`           | string  | `"movie.mkv"`                  |
| `extension`          | string  | `"mkv"`, `"srt"`               |
| `category`           | string  | `media`, `subtitle`, `junk`    |
| `container`          | string  | `matroska`, `mp4`              |
| `video_codec`        | string  | `hevc`, `h264`, `vp9`          |
| `audio_codec`        | string  | `eac3`, `aac`                  |
| `subtitle_codec`     | string  | `subrip`, `pgs`                |
| `width` / `height`   | integer | `1920`, `3840`                 |
| `duration_seconds`   | number  | `7200.5`                       |
| `bitrate_kbps`       | integer | `15000`                        |
| `framerate`          | number  | `23.976`                       |
| `size_bytes`         | integer | `10737418240`                  |
| `has_subtitles`      | boolean | `true`, `false`                |
| `is_orphaned`        | boolean | `true`, `false`                |
| `subtitle_languages` | array   | `["eng", "spa"]`               |
| `audio_languages`    | array   | `["eng", "jpn"]`               |
| `tags`               | array   | `["4k", "missing-subs:en"]`    |

## Operators

| Field type | Operators                                |
|------------|------------------------------------------|
| numeric    | `eq`, `ne`, `lt`, `lte`, `gt`, `gte`     |
| boolean    | `eq`, `ne`                               |
| string     | `eq`, `ne`, `in`, `regex`                |
| array      | `contains`, `not_contains`, `any_of`, `none_of` |

`regex` is full Python re; invalid expressions don't match (they don't
error). `in` takes a list value and tests for membership.

## Severity scale

| Label   | Rank |
|---------|------|
| `ok`    | 10   |
| `info`  | 20   |
| `warn`  | 40   |
| `high`  | 60   |
| `error` | 80   |
| `crit`  | 100  |

**Severity is monotonic.** Multiple rules matching a file combine by
taking the **maximum** severity any matching rule would apply. Rule
order does not matter.

## Actions

| Action               | Payload                                      |
|----------------------|----------------------------------------------|
| `set_severity`       | `severity`: one of the labels above          |
| `add_tag`            | `tag`: string (≤ 64 chars)                   |
| `queue_optimization` | `profile`: optimization profile name         |
| `notify`             | `channel`: string; optional `message`        |

Rule-added tags carry `source="rule"` in `media_tags`. Tags from
integrations (Sonarr/Radarr/Bazarr) carry the integration's `kind` as
their source. Rules can match on **any** tag regardless of source.

## When rules run

- Automatically after every scan completes (per library). The scanner
  picks up every file in the affected library and runs all enabled
  rules.
- Manually via **Rules → Evaluate** for a library, or
  `POST /api/v1/rules/libraries/{id}/evaluate`.
- In **dry-run** mode against a single file via the API
  (`POST /api/v1/rules/dry-run`) — no persistence; used by the editor
  to preview matches.

## Bad rules are skipped, not fatal

The evaluator parses each enabled rule's definition before running. If
a rule's JSON has drifted from the schema (because a field was removed
or an operator was renamed in a future version), that single rule is
logged and skipped. Other rules keep running.
