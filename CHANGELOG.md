# Changelog

## v6 — 2026-05-08

Stability and bug-fix release. Focused on database resilience across upgrades, fixing crashes, and reorganising the rules UI.

### Fixed

- **Couldn't add automation rules.** Migration system now ensures `action_config`, `file_category`, `severity_match`, `runs_count`, and `last_action_count` columns exist on every install; server endpoint validates required fields and returns useful errors.
- **Databases broke over time.** New schema-versioning system with 5 idempotent migrations (v0–v4). v0 backfills any missing base columns on `files`, `evaluations`, `scans`, `integrations`, `integration_events`, and `custom_rules` so users coming from old builds get a complete schema. Migrations run inside `init()` and are safe to apply repeatedly.
- **Re-eval Rules triggered a library scan.** Re-eval is now genuinely rules-only — no disk reads, no ffprobe, no subtitle revalidation. Full Scan asks for confirmation before walking the filesystem. UI button labels update on the correct buttons (`_kickoff` was updating the wrong element).
- **Sonarr/Radarr events stopped being tracked.** Column-name mismatch (`raw` vs `payload`) caused `add_integration_event` to fail silently; corrected.
- **App crashed once a scan completed.** The post-scan automation runner threw on missing columns. Hardened with try/except around every rule and around integration setup.
- **OK files didn't appear when filtering by severity.** Clean files have no `evaluations` rows, so the old filter returned nothing. Rewritten so each severity filter matches files where that's the headline severity; `ok` matches files with no issues at all.
- **Category sorting was inconsistent.** Files now sort by `COALESCE(sev_rank, 0) DESC`.
- **Glob ignore patterns missed directory components.** `_UNPACK_*` now matches files in directories whose names match the pattern.

### Added

- **Backup / Restore.** `GET /api/db/backup` streams a consistent SQLite snapshot using the online backup API. `POST /api/db/restore` accepts an upload, validates the schema, then replaces the live DB. Handles WAL state so backups taken under load are valid and restores don't corrupt the live DB.
- **DB maintenance UI.** Settings → Database shows path, size, file/eval/integration/rule counts, and schema version. Vacuum, Integrity check, Clean evaluations buttons.
- **Auto-save settings.** All settings inputs save automatically with a 600 ms debounce. Status indicator shows "Saving…" / "Saved" / "Save failed".
- **Help page.** New nav item with three tabs: README, Changelog, Links. README and changelog are rendered from the bundled markdown files using an inline renderer (headers, lists, tables, code, links).
- **Custom Rules redesign.** Three tabs:
  - **Built-in** — all 55 named rules visible and grouped by category. Toggle on/off, override severity, or drop entirely.
  - **Custom** — user-created rules.
  - **Disabled / Discarded** — dropped rules, with a Restore action.
- **Severity match modes.** Automation rules can choose `highest` (default), `lowest`, or `any` when comparing a file's multiple severities against the rule threshold.

### Notes

- Schema version tracked in `schema_meta`; `db.schema_version()` returns it; UI shows it under Settings → Database.
- Backup files are valid SQLite databases; you can open them with any SQLite tool offline.
- Restore performs a sanity check on the uploaded file (must contain core tables) before replacing the live DB.

---

## v5

- Single-user auth with PBKDF2 hashing, session cookies, and an API token.
- GitHub commit poller with notify-only update banner.
- Full Bazarr integration: subtitle sync, webhook, delete-subtitle and search-subtitles actions.
- Full Tdarr integration: library/plugin sync, three transcode-queue modes, longest-prefix-match remote path mappings.
- Extended automation: `transcode_via_tdarr`, `search_subs_via_bazarr`, `delete_sub_via_bazarr` actions; file-category restrictions.
- Dashboard redesign: Media-centric layout with clickable everything; non-media categories on the side.

## v4 and earlier

- 6-level severity scale (`unplayable` → `ok`).
- Custom rule engine with visual builder and raw JSON editor.
- Plex + Jellyfin device matrix (28 devices in Both mode).
- File categorisation with ignore patterns; ignored files never enter the database.
- Full Sonarr/Radarr integration with monitoring control.
- Three scan modes: Full Scan, Re-eval Rules, Targeted.
