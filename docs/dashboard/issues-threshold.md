---
id: dashboard/issues-threshold
title: Issues threshold
category: dashboard
tags: [dashboard, issues, severity, settings]
summary: Configure the minimum severity that surfaces in the dashboard's Issues tile.
help_context: [dashboard.issues, settings.runtime.dashboard]
related: [dashboard/overview, rules/severity]
---

# Issues threshold

The Dashboard's **Issues** tile counts files at or above a configured
minimum severity. By default the threshold is `warn`, so:

- `info` files are excluded (they're informational).
- `warn`, `high`, `error`, and `crit` files are counted.

This keeps the tile from being dominated by low-signal noise on a
fresh install where many files have informational severity from
extension rules or built-in checks.

## Changing the threshold

Settings → Runtime → `dashboard_issue_min_severity`. The accepted
values are: `info`, `warn`, `high`, `error`, `crit`.

The setting is hot — changing it does **not** require a restart. The
dashboard refetches on the next interval (or when an event over the
WebSocket triggers an invalidation).

## Per-page filter vs runtime threshold

The Issues tile uses the runtime threshold as a global floor.
Individual screens (the Files page, rule detail) have their own
in-page severity filters that operate independently — they default
to "all severities" and don't read the runtime threshold.

## Choosing a value

| Value | When to pick it |
|---|---|
| `info` | You want every flagged row in the count — useful during initial library cleanup. |
| `warn` | Default. Hides info-only noise; surfaces anything worth a glance. |
| `high` | You only want to see things the rule engine flagged as actionable. |
| `error` / `crit` | You're triaging a known incident and want to ignore everything else. |

## See also

- [Severity levels](/help/rules/severity) — what each level means.
- [Rule actions](/help/rules/actions) — how rules set severity.
