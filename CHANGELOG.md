# Changelog

## v7 — 2026-05-09

Quality + automation release. Two big shifts: severity now has separate scales for media vs non-media files, and the updater can install new versions automatically without `git pull`.

### Fixed

- **Junk category showed 5000+ files but clicking it returned nothing.** Junk files were getting categorised but never received any evaluations, so the file browser's "headline severity" filter rejected them all. Fixed by ensuring every junk file gets at least one issue (`file_unknown_extension`), so the entire category is now browseable.
- **Click-through filters from the dashboard returned no files.** Codec / audio codec / resolution clicks were dumping the value into the search box (which searches paths). They now use proper filter state and show as removable pills above the file list.
- **Severity filter for non-media files.** The headline-severity SQL only knew the media scale; non-media files with `corrupt`/`warning`/`possible_malicious` were misordered or filtered out incorrectly. The SQL now ranks both scales correctly, and `_expand_file_row` reads the headline severity directly from the query rather than mapping ranks back to names.
- **Stats showed phantom codec / audio / resolution entries.** Empty strings in those columns were counted as their own group; now filtered out.

### Added

- **Dependency check on startup.** New `deps.py` module verifies `flask`, `apscheduler`, `ffprobe`, and `ffmpeg` are present. If anything's missing, prints a clear report to stderr with exact install commands per distro (Debian, Fedora, Arch, Alpine, macOS, Windows) and the expected install path. Exposed via `GET /api/health`. Optional `--install-deps` CLI flag attempts pip-install for missing Python packages (with sudo if needed).
- **Settings → Dependencies card.** UI surface for the same data; "Auto-install Python packages" button.
- **Branch picker for updates.** Switch between `main` (stable) and `dev` (bleeding edge) directly in the UI. Selection persists in `.auditarr_version.json`.
- **Seamless install of updates.** The Settings → Updates section now has an "Install update" button that downloads the tarball, extracts it, and copies files into the install directory atomically (using temp+rename per file). User data — `config.json`, `auth.json`, `media_audit.db*`, `.auditarr_version.json` — is never overwritten. Restart Auditarr after install completes.
- **Split severity model.** Media files keep the original 6-level scale (`unplayable` / `always_transcode` / `possible_transcode` / `high_bitrate` / `info` / `ok`). Non-media files (subtitle, image, metadata, junk) use a new 5-level scale: `ok` / `info` / `warning` / `corrupt` / `possible_malicious`. The file browser's filter chips show only the severities relevant to the current category.
- **Non-media rules.** New rules: `junk_executable` (executable extensions in a media library → `possible_malicious`), `junk_archive` (`.rar`/`.zip`/`.7z` leftovers → `warning`), `junk_large` (oversized junk → `warning`), `junk_empty` (0-byte junk → `corrupt`), `image_too_large` (>50 MB artwork → `warning`), `metadata_too_large` (>5 MB NFO → `warning`). Subtitle issues (orphan, invalid, unreadable) now use the non-media scale too.
- **Severity tile metadata.** Each tile on the dashboard now shows the count of unique rules that fire at that severity, plus the next-more-severe and next-less-severe neighbour counts.

### Notes

- The unified SQL ranking treats both scales as parallel: rank 5 = `unplayable` or `possible_malicious`; rank 4 = `always_transcode` or `corrupt`; rank 3 = `possible_transcode`; rank 2 = `high_bitrate` or `warning`; rank 1 = `info`; rank 0 = `ok`. Severity names are unique across both scales so filters and headline-severity lookups never collide.
- `BUILTIN_RULES` registry now tracks 60+ rules total. Each can be toggled, severity-overridden, or dropped from Custom Rules → Built-in tab.
- The updater never overwrites the protected files even if a future commit changes their tracked-version. Local schema migrations apply on the next start.

---

## v6

Stability and bug-fix release. Schema migration system, backups, auto-save settings, help page, custom rules redesign, severity match modes, dozens of bug fixes.

## v5

Authentication, GitHub commit poller (notify-only), full Bazarr/Tdarr integrations, dashboard redesign.

## v4 and earlier

6-level severity scale, custom rule engine, Plex+Jellyfin device matrix (28 devices), Sonarr/Radarr full integration, three scan modes.
