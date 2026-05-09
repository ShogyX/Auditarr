# Auditarr

A web-based media library auditor for Plex/Jellyfin/Sonarr/Radarr/Bazarr/Tdarr setups. Walks your library, stores metadata in SQLite, evaluates files against a comprehensive compatibility ruleset (with custom rules), and acts on findings via integrations.

GitHub: [https://github.com/ShogyX/Auditarr](https://github.com/ShogyX/Auditarr)

## Run

```bash
pip install flask apscheduler
python3 server.py
# → http://localhost:7842
```

Requires `ffprobe` (from `ffmpeg`) on PATH. Tested on Python 3.10+.

On first launch, the browser will redirect you to `/login.html` to set up an admin account. Auditarr generates an API token at the same time — copy it and store it somewhere safe (it's also visible later in Settings → Account).

## What's new in v6

Stability and bug-fix release.

- **Robust schema migrations.** A new versioning system means upgrading from any older Auditarr build no longer corrupts your database. Run the new build and it will detect missing columns/tables, add them, and continue. All migrations are idempotent.
- **Backup and restore.** Settings → Database has a "Download backup" button that produces a consistent SQLite snapshot, and a "Restore from backup" button that uploads a backup file and replaces the live database. Restore is sanity-checked before applying.
- **Auto-save settings.** No more "Save Configuration" button — every change to settings (paths, ignore patterns, ranges, toggles, compatibility mode) saves automatically with a small status indicator.
- **Help & About page.** A new nav item shows the bundled README, a CHANGELOG, and links to GitHub/issues/related projects. Rendered with a tiny inline markdown renderer.
- **Custom Rules redesign.** Three tabs: Built-in (55 named rules grouped by category, all toggleable, severity-overridable, or droppable), Custom, and Disabled/Discarded.
- **Severity match modes.** Automation rules can decide how to compare a file with multiple severities against the rule threshold: highest (default), lowest, or any.

### Bugs fixed

- Could not add automation rules (missing columns on existing DBs).
- App crashed at the end of a scan (post-scan automation runner threw on missing columns).
- "Re-eval Rules" was triggering a library scan (subtitle revalidation read disk).
- Sonarr/Radarr webhook events stopped being recorded (column-name mismatch).
- OK-severity filter returned no files (clean files have zero evaluations, not a `severity='ok'` row).
- Category sorting put files with no evaluations in the wrong order.
- `_UNPACK_*` ignore pattern missed files inside `_UNPACK_*` directories.

## Authentication

When auth has not been configured, navigating to `/` redirects to a setup page. Choose a username and a password (≥ 8 chars). After setup the API token is shown once — copy it immediately.

| How to authenticate | Where |
| --- | --- |
| Browser cookie session | Set automatically after `/api/auth/login`; HttpOnly + SameSite=Strict, 14-day TTL |
| `Authorization: Bearer <token>` | Any API request |
| `X-API-Key: <token>` | Any API request (alternative header) |

Webhook endpoints (`/api/integrations/webhook/<id>`) are intentionally **public** so Sonarr/Radarr/Bazarr can POST to them without credentials. The webhook URL itself is the secret.

To rotate the API token: Settings → Account → ↻ next to the token field. Existing scripts will need to be updated.

## Auto-update (notify-only)

Auditarr polls `https://api.github.com/repos/ShogyX/Auditarr/commits/main` every 6 hours. Public repo, no GitHub token needed. When the latest SHA differs from your stored SHA, a banner appears at the top of the UI:

> ⬆ **New version available** — *commit message* `abc1234` &nbsp; [I've updated] [View on GitHub] [Dismiss]

Update workflow:

```bash
cd /path/to/auditarr
git pull
python3 server.py
```

The app will run any new schema migrations automatically. Then click *Mark current as latest* (or *I've updated* in the banner) so Auditarr stops nagging.

## Database management (new)

Settings → Database shows:

- Live DB path and size on disk.
- Counts of files, evaluations, integrations, automation rules, custom rules.
- Current schema version.

Buttons:

- **⬇ Download backup** — streams a consistent SQLite snapshot.
- **⬆ Restore from backup** — uploads a backup file and replaces the live DB.
- **⚬ Vacuum** — reclaims space and rebuilds the file.
- **⚡ Integrity check** — runs `PRAGMA integrity_check`.
- **⌫ Clean evaluations** — drops every issue row (files stay; run *Re-eval Rules* to repopulate).

## Auto-save settings

Every input on the Settings page saves automatically. The indicator next to the *Force save* button shows the last save state ("Saving…", "Saved", "Save failed"). The Force save button is a fallback if you've changed something and want to flush immediately.

## Built-in vs custom rules

There are now three kinds of rules in Auditarr:

1. **Built-in** rules — implemented in code; 55 named rules covering DV, HEVC/AV1/H.264 variants, audio codecs, container quirks, HDR, subtitles, framerate, resolution, and bitrate. Each has a default severity but you can:
   - Toggle it off (still listed in the Built-in tab, just doesn't run).
   - Override its severity.
   - Drop it (moves to the Disabled/Discarded tab and stops running).
2. **Custom** rules — user-created via the visual builder or raw JSON editor. 16 fields, 11 operators.
3. **Dropped** rules — built-in or custom rules a user has explicitly removed; can be restored from the Disabled/Discarded tab.

After changing rules, click **⚡ Re-eval Rules** in the sidebar — it's instant and doesn't read any media files.

## Severity match modes (automation)

When an automation rule fires depends on which severity from a multi-issue file you compare:

| Mode | Meaning |
| --- | --- |
| **Highest** (default) | "The worst issue's severity is at least X." Old behaviour. |
| **Lowest** | "Even the least-severe issue meets X." Useful for narrow rules that should fire only when literally everything matches. |
| **Any** | "At least one issue matches." Good when you want a rule to fire on any file with even a single qualifying issue. |

## Severity scale

| Severity | Meaning |
| --- | --- |
| **Unplayable** | File has issues or formats Plex/Jellyfin can't play |
| **Always Transcode** | Will always transcode (Chrome web client baseline) |
| **Possible Transcode** | Some clients won't direct-play |
| **High Bitrate** | Above your configured threshold (default 80 Mbps) |
| **Info** | Worth noting but generally fine |
| **OK** | Direct-plays on most clients |

## Compatibility modes

Settings → Compatibility Mode:

- **Plex only** — 17 Plex devices in the matrix
- **Jellyfin only** — 11 Jellyfin clients (Web, JMP, Android, Roku, Swiftfin, Kodi, Infuse…)
- **Both** — 28 devices grouped by ecosystem

Jellyfin overrides reflect real-world differences from Plex — e.g. Jellyfin Media Player (mpv-based) handles HEVC 10-bit, ASS subtitles and TrueHD natively; DV passthrough is broken on most Jellyfin clients except Infuse.

## File categories

- **Media** — `.mkv`, `.mp4`, `.avi`, `.mov`, `.ts`, `.m2ts`, `.webm`
- **Subtitle** — `.srt`, `.ass`, `.ssa`, `.sub`, `.vtt`, `.idx`, `.sup`, `.smi`
- **Image** — `.jpg`, `.png`, `.webp`
- **Metadata** — `.nfo`, `.xml`, `.txt`, `.sfv`
- **Junk** — anything else

Files matching any pattern in `ignore_patterns` are skipped entirely.

### Ignore patterns (globs)

| Pattern | Matches |
| --- | --- |
| `.plexmatch` | Exact filename |
| `*.tmp` | Any file ending in `.tmp` |
| `_UNPACK_*` | Any file in a directory starting with `_UNPACK_` |
| `@eaDir` | Any file inside an `@eaDir` folder |

Newly-added patterns are applied during the next *Full Scan* — files that newly match are removed from the database.

## Three scan modes

| Mode | Speed | Use when |
| --- | --- | --- |
| **Run Full Scan** | minutes/hours | First scan, or library has changed substantially |
| **⚡ Re-eval Rules** | seconds | Rules updated, threshold changed — applies new logic to stored ffprobe data; **does not read any files from disk** |
| **Targeted Scan** | per-file | Re-probe specific files; webhook handler uses this automatically |

## Integrations

| Plugin | Test | Sync | Webhook | Automation |
| --- | --- | --- | --- | --- |
| **Sonarr** | ✓ | ✓ | ✓ | ✓ |
| **Radarr** | ✓ | ✓ | ✓ | ✓ |
| **Bazarr** | ✓ | ✓ (subtitle inventory) | ✓ | ✓ (search/delete subs) |
| **Tdarr** | ✓ | ✓ (libraries + plugins) | ✓ | ✓ (queue transcode) |
| **Plex** | ✓ | — | — | — |
| **Jellyfin** | ✓ | — | — | — |

## Project structure

```
auditarr/
├── server.py              Flask app, scheduler, auth middleware, all REST endpoints
├── auth.py                PBKDF2 single-user auth + API token + sessions
├── updater.py             GitHub commit poller
├── db.py                  SQLite schema, migrations, queries, backup/restore
├── checks.py              Built-in rule engine + Plex/Jellyfin device map + glob ignore + BUILTIN_RULES registry
├── scanner.py             Scan orchestration with custom/built-in rule application
├── integrations/
│   ├── __init__.py        Registry, polling worker, automation runner with severity_match
│   ├── base.py            Integration base class
│   ├── sonarr_radarr.py   Full Sonarr + Radarr
│   ├── bazarr.py          Full Bazarr
│   ├── tdarr.py           Full Tdarr with path-mapping
│   └── scaffolds.py       Plex / Jellyfin stubs
├── frontend/
│   ├── index.html         Main UI with new dashboard, rules tabs, help, DB management
│   ├── login.html         First-run setup + login page
│   └── app.js             Frontend logic
├── README.md              This file
├── CHANGELOG.md           Version history
├── config.json            User config (auto-created)
├── auth.json              Single-user credentials (mode 0600, auto-created)
├── .auditarr_version.json Tracked SHA for the update checker
└── media_audit.db         SQLite store (auto-created)
```

## REST API (selected new endpoints)

```
─── Database ───
GET    /api/db/stats                    schema version, sizes, counts
GET    /api/db/backup                   download SQLite snapshot
POST   /api/db/restore                  multipart upload to replace live DB
POST   /api/db/vacuum
GET    /api/db/integrity
POST   /api/db/clean                    drop all evaluations

─── Built-in rules ───
GET    /api/rules/builtin
PUT    /api/rules/builtin/<rule_key>    {"enabled":bool, "dropped":bool, "severity_override":str|null}
GET    /api/rules/dropped

─── Help ───
GET    /api/help/readme
GET    /api/help/changelog

─── Automation (new fields accepted) ───
POST   /api/automation/rules            now accepts severity_match: "highest"|"lowest"|"any"
PUT    /api/automation/rules/<id>       same
```

All routes carried over from previous versions; auth requirements unchanged.

## Upgrade notes

If you're upgrading from v4 or v5, just run the new `server.py`. On first launch, migrations will detect the schema you have, add the missing columns, purge any stray `category='ignored'` rows, and bump the schema version to 4. Your data — files, evaluations, integrations, automation rules, custom rules — is preserved.

If anything looks off, **back up first** (Settings → Database → Download backup), then if needed restore by re-uploading the backup file (this also re-runs migrations). The integrity check button confirms the SQLite file is clean.
