---
id: system/factory-reset
title: Factory reset
category: system
tags: [system, reset, danger]
summary: Wipe Auditarr's state back to a fresh-install baseline.
help_context: [system.factory-reset]
related: [system/logs]
---

# Factory reset

Factory reset wipes Auditarr's runtime state — every library, integration, rule, playback event, audit log entry, and tag — back to a clean slate. The application restarts immediately after the wipe with the same admin user(s) as before. Disk-level media files are **not touched**.

## When to use it

* Testing a fresh-install behavior on a staging environment.
* Recovering from a corrupted database that's faster to wipe than to repair.
* Demoing the app from zero state.

For everything else, prefer the per-row deletes (libraries, integrations, rules) so you don't lose tangential state you actually needed.

## How to trigger

Available under `System → Reset` for admins, behind a typed-confirmation gate. The page lists exactly what will be wiped and what's preserved, asks the operator to type `RESET AUDITARR` into a text input, then enables the destructive button.

## What gets wiped

* `media_files`, `media_tags`, `library`, `integration` (and all per-kind config), `rule` (custom only — built-ins reseed on startup), `rule_evaluation`, `rule_suggestion`, `playback_event`, `playback_session`, `playback_device`, `scan_run`, `job_run`, `optimization_item`, `optimization_profile`, `audit_log_entry`, `vt_queue`, `notification_throttle`, `runtime_setting_override`, `runtime_setting_change`, `media_extension_rule`, `automation_schedule`, `path_mapping`, `integration_secret`.

## What's preserved

* `user` and `auth_session` rows for admin users. Non-admin users are removed. The operator's session continues without a forced logout.
* The application's secret key (`AUDITARR_SECRET_KEY`).
* The on-disk plugin directory (`plugin_dir`). Plugins remain installed; their per-plugin databases are emptied.
* Disk-level media files. Auditarr never deletes files outside its own `data_dir/trash/` directory.

## After reset

The first action the operator takes on a reset install should be adding a library. The next scan re-indexes the disk-level media. Rule history, playback history, and audit trail are gone — the freshly-indexed files start at severity `ok`.
