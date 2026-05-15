---
id: rules/conditions
title: Rule conditions
category: rules
tags: [rules, conditions]
summary: Building blocks for matching files in the rules engine.
help_context: [rules.conditions]
related: [rules/actions, rules/severity]
---

# Rule conditions

A rule's `conditions` array describes which files it applies to. Each
condition is a small JSON object with a `field`, `operator`, and `value`.

## Built-in operators

| Operator         | Description                                             |
|------------------|---------------------------------------------------------|
| `eq` / `ne`      | Equality / inequality                                   |
| `gt` / `lt`      | Strictly greater / less than                            |
| `gte` / `lte`    | Greater-or-equal / less-or-equal                        |
| `in` / `not_in`  | Member of a set                                         |
| `matches`        | Regex match against a string                            |
| `present`        | Field is non-null                                       |

## Common fields

- `video_codec`, `audio_codec`, `subtitle_codec`
- `bitrate_kbps`, `width`, `height`, `duration_seconds`
- `container`, `extension`
- `tags` (from Sonarr/Radarr tag sync)

## Example

```json
{
  "field": "video_codec",
  "operator": "not_in",
  "value": ["hevc", "av1"]
}
```

Plugins can register additional operators and fields through the SDK.
