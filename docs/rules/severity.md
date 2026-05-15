---
id: rules/severity
title: Severity scopes
category: rules
tags: [severity, rules]
summary: Severities are user-editable; built-in scopes can be extended.
help_context: [rules.severity, settings.severities]
related: [rules/conditions, rules/actions]
---

# Severity scopes

Severities are entirely data-driven — there are no hardcoded names or
levels. The defaults shipped on first boot are illustrative; you can rename,
reorder, disable, or replace them.

## Default severities

| Name      | Rank | Color   |
|-----------|------|---------|
| `ok`      | 10   | green   |
| `info`    | 20   | blue    |
| `warn`    | 50   | amber   |
| `high`    | 70   | orange  |
| `error`   | 90   | red     |
| `crit`    | 100  | magenta |

Higher rank = more severe. Notifications, dashboard heatmaps, and
filterable views all key off `rank`, not name — so renaming is safe.

## Scopes

A severity can be assigned to one or more scopes:

- `media` — file-level findings
- `subtitle` — subtitle-related findings
- `metadata` — metadata-related findings
- `image` — artwork-related findings
- `junk` — junk file detection
- `optimization` — optimization queue health
- `integration` — integration availability

Plugins can declare new scopes by registering them at startup.
