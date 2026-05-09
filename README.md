# Auditarr

A web-based media library auditor for Plex/Jellyfin/Sonarr/Radarr/Bazarr/Tdarr setups. Walks your library, stores metadata in SQLite, evaluates files against a comprehensive compatibility ruleset, and acts on findings via integrations.

GitHub: [https://github.com/ShogyX/Auditarr](https://github.com/ShogyX/Auditarr)

## Run

```bash
pip install flask apscheduler
python3 server.py
# → http://localhost:7842
```

Requires `ffprobe` (from `ffmpeg`) on PATH. Tested on Python 3.10+.

On first launch, Auditarr now checks all dependencies. If anything's missing it prints a clear report listing each item, the command to install it, and the path it expects to find it on. To attempt automatic install of Python packages:

```bash
python3 server.py --install-deps   # uses sudo if needed
```

The browser will redirect you to `/login.html` to set up an admin account. Auditarr generates an API token at the same time — copy it and store it somewhere safe (it's also visible later in Settings → Account).

## What's new in v7

- **Dependency check at startup.** Auditarr verifies `flask`, `apscheduler`, `ffprobe`, and `ffmpeg` are available before serving requests. Missing items get a per-distro install command and an expected path. Status is also shown in Settings → Dependencies.
- **Update branches + automatic install.** Pick `main` (stable) or `dev` (bleeding edge) in Settings → Updates. When an update is available, click *Install update* and Auditarr downloads the tarball, extracts it, and replaces files in the install directory automatically. Your config, auth, and database are never touched. Restart Auditarr to apply.
- **Split severity model.** Media files use the original 6-level scale. Non-media files (subtitle, image, metadata, junk) now use a separate scale: `ok` / `info` / `warning` / `corrupt` / `possible_malicious`. Cleaner mental model and more accurate triage.
- **All junk files are visible.** Every junk file gets at least one issue (`file_unknown_extension`) so the entire category is browseable. New rules flag executables in your library as `possible_malicious`, archive leftovers as `warning`, oversized files as `warning`, etc.
- **Click-through filters work properly.** Click a codec, audio codec, or resolution on the dashboard and the file browser filters by that exact value. Active filters appear as removable pills above the file list.
- **Severity tiles show neighbouring counts.** Each tile on the dashboard now shows how many unique rules fire at that severity, plus the next-more-severe and next-less-severe counts so you can see where files are concentrated.

### Bugs fixed in this release

- Junk showed 5000+ in stats but clicking returned no files
- Codec/audio/resolution clicks searched paths instead of filtering metadata
- Non-media severity filtering was wrong (clean files appeared, dirty ones didn't)
- Stats showed phantom codec entries for files with empty codec strings
- Dashboard counts didn't match file browser results

## Severity scales

### Media (`category=media`)

| Severity | Meaning |
| --- | --- |
| **Unplayable** | File has issues or formats Plex/Jellyfin can't play |
| **Always Transcode** | Will always transcode (Chrome web client baseline) |
| **Possible Transcode** | Some clients won't direct-play |
| **High Bitrate** | Above your configured threshold (default 80 Mbps) |
| **Info** | Worth noting but generally fine |
| **OK** | Direct-plays on most clients |

### Non-media (`category=subtitle|image|metadata|junk`)

| Severity | Meaning |
| --- | --- |
| **Possible Malicious** | Executable file extensions found in a media library |
| **Corrupt** | Empty or unreadable file |
| **Warning** | Archive leftover, oversized image, orphan subtitle, etc. |
| **Info** | Routine — file recorded but no concerns |
| **OK** | No issues |

The dashboard shows the right scale for each category card. The file browser's severity chips automatically swap to match whichever category you're viewing.

## Updates

Auditarr polls GitHub every 6 hours for new commits on the branch you're tracking (`main` or `dev`).

### Manual update (still supported)

```bash
cd /path/to/auditarr
git pull
python3 server.py
```

Click *Mark current as latest* in Settings → Updates after pulling so Auditarr stops nagging.

### Automatic install

Click *Install update* in Settings → Updates. Auditarr will:

1. Download the tarball from `codeload.github.com` for the current branch
2. Extract to a temp dir
3. Copy files to the install directory (atomic per-file: write to `.new` then `os.replace`)
4. Skip any file in `PROTECTED_FILES`: `config.json`, `auth.json`, `media_audit.db*`, `.auditarr_version.json`
5. Mark the new SHA as current

You'll need to restart Auditarr after the install to load the new code. Schema migrations run automatically on next start.

### Switching branches

Settings → Updates → branch pills (`main` / `dev`). The selection persists. Next update check will look at that branch's HEAD.

## Dependencies

`deps.py` checks for:

- **Python packages** — `flask`, `apscheduler`. If missing, the app exits with a clear install command.
- **Binaries** — `ffprobe`, `ffmpeg`. If missing, the app starts in degraded mode (UI loads, scans fail) and shows a banner.

Per-distro install commands shown in Settings → Dependencies and on stderr at startup. Auto-install button attempts `pip install` (with sudo if needed) for Python packages only — binaries you'll need to install yourself with your distro's package manager.

## Authentication

When auth has not been configured, navigating to `/` redirects to a setup page. Choose a username and a password (≥ 8 chars). After setup the API token is shown once — copy it immediately.

| How to authenticate | Where |
| --- | --- |
| Browser cookie session | Set automatically after `/api/auth/login`; HttpOnly + SameSite=Strict, 14-day TTL |
| `Authorization: Bearer <token>` | Any API request |
| `X-API-Key: <token>` | Any API request (alternative header) |

Webhook endpoints (`/api/integrations/webhook/<id>`) are intentionally **public** so Sonarr/Radarr/Bazarr can POST without credentials. The webhook URL itself is the secret.

## File categories

- **Media** — `.mkv`, `.mp4`, `.avi`, `.mov`, `.ts`, `.m2ts`, `.webm`
- **Subtitle** — `.srt`, `.ass`, `.ssa`, `.sub`, `.vtt`, `.idx`, `.sup`, `.smi`
- **Image** — `.jpg`, `.png`, `.webp`
- **Metadata** — `.nfo`, `.xml`, `.txt`, `.sfv`
- **Junk** — anything else

Files matching any pattern in `ignore_patterns` are skipped entirely.

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
| **Bazarr** | ✓ | ✓ | ✓ | ✓ (search/delete subs) |
| **Tdarr** | ✓ | ✓ | ✓ | ✓ (queue transcode) |
| **Plex** | ✓ | — | — | — |
| **Jellyfin** | ✓ | — | — | — |

## Project structure

```
auditarr/
├── server.py              Flask app, scheduler, auth middleware, all REST endpoints
├── auth.py                PBKDF2 single-user auth + API token + sessions
├── deps.py                Dependency check + auto-install (NEW v7)
├── updater.py             GitHub poller + tarball-based seamless install (REWRITTEN v7)
├── db.py                  SQLite schema, migrations, queries, backup/restore
├── checks.py              Built-in rule engine, BUILTIN_RULES registry, non-media rules
├── scanner.py             Scan orchestration with custom/built-in rule application
├── integrations/          Sonarr / Radarr / Bazarr / Tdarr / Plex / Jellyfin
├── frontend/
│   ├── index.html         Main UI
│   ├── login.html         First-run setup + login
│   └── app.js             Frontend logic
├── README.md / CHANGELOG.md
├── config.json            User config (auto-created)
├── auth.json              Single-user credentials (mode 0600, auto-created)
├── .auditarr_version.json Tracked SHA + branch (auto-created)
└── media_audit.db         SQLite store (auto-created)
```

## REST API (selected new endpoints)

```
─── Health & deps ───
GET    /api/health                       deps check report
POST   /api/install-deps                 attempt auto-install of Python pkgs

─── Updates ───
GET    /api/update/check                 current state + branch + latest SHA
POST   /api/update/refresh               force GitHub poll
GET    /api/update/branches              available branches with availability
POST   /api/update/branch                {"branch": "main"|"dev"}
POST   /api/update/install               download + apply tarball
POST   /api/update/mark-current          {"sha": "..."}  manual confirmation
```

All carried over from v6 still work; auth requirements unchanged.

## Upgrade notes

If you're upgrading from any earlier version, just run the new `server.py`. On first launch:

1. Schema migrations apply (versioned + idempotent)
2. Dependency check runs (warns if anything's missing)
3. Update poller starts on the branch from `.auditarr_version.json` (defaults to `main`)
4. Server starts on `http://localhost:7842`

Your data — files, evaluations, integrations, automation rules, custom rules — is preserved.
