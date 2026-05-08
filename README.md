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

## What's new in v5

- **Authentication** — single-user login with PBKDF2-hashed password and an API token for headless/script use. Sessions are HttpOnly cookies; the API token can be sent as `Authorization: Bearer <token>` or `X-API-Key: <token>`.
- **Self-update notifier** — Auditarr polls GitHub every 6 hours for new commits on `main`. When found, a banner offers a link to view the diff; pull manually with `git pull` (or download a tarball) and click *Mark current as latest* to dismiss.
- **Bazarr integration** — full sync of subtitle inventory, webhook listener for `Subtitle Downloaded` / `Subtitle Removed` events, and outbound actions to delete a subtitle file or trigger a re-search for a media item.
- **Tdarr integration** — full integration with libraries, plugins, queue inspection, and three modes for queueing transcodes:
  1. *Library mode* — pick an existing Tdarr library (uses its configured flow)
  2. *Plugin mode* — pick a community Flow / plugin by name
  3. *Inline profile* — define codec / container / CRF / hardware-accel right in Auditarr; the spec is sent to Tdarr's GenericTranscode flow
- **Remote path mappings** — for Tdarr running in a different container, configure `local → remote` path translations on the integration; longest-prefix-match wins. Files queued for transcode are translated automatically.
- **Extended automation rules** — actions now include `monitor` / `unmonitor` (Sonarr/Radarr), `transcode_via_tdarr`, `search_subs_via_bazarr`, and `delete_sub_via_bazarr`. Rules can also be restricted to a specific file category.
- **Dashboard redesign** — Media stats are the default and central panel. Subtitles / Images / Metadata / Junk show as compact side cards with mini severity pills. Every stat (severity tile, codec bar, audio bar, resolution bar, highlight tile, side card, severity pill) is clickable and filters the file browser to the matching subset.

## What was already there (v4 carryover)

- 6-level severity scale: `Unplayable`, `Always Transcode`, `Possible Transcode`, `High Bitrate`, `Info`, `OK`. A file's headline severity is the worst across all its issues.
- Custom rule engine with both visual builder and raw-JSON editor; 16 fields and 11 operators.
- Plex + Jellyfin device matrix (28 devices total when *Both* mode is active).
- File categorisation: `media`, `subtitle`, `image`, `metadata`, `junk` — with `ignored` files never stored in the DB.
- Three scan modes: *Full Scan*, *Re-eval Rules* (no ffprobe), *Targeted* (single file or webhook-driven).

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
# (or download a release tarball)
python3 server.py
```

Then click *Mark current as latest* (or *I've updated* in the banner) so Auditarr stops nagging.

## Bazarr integration

| Feature | What it does |
| --- | --- |
| Test | Pings `/api/system/status` |
| Sync | Walks `/api/series` and `/api/movies`, links subtitle inventory to files in Auditarr's DB |
| Webhook | Receives Bazarr's *Subtitle Downloaded* / *Subtitle Removed* notifications and triggers a targeted re-scan |
| Delete subtitle | UI button in file detail; calls Bazarr's `PATCH /api/{episodes,movies}/subtitles` with `action=delete` |
| Search subtitles | UI button in file detail; calls the same endpoint with `action=search` |
| Automation | Rules can `search_subs_via_bazarr` or `delete_sub_via_bazarr` based on file severity |

## Tdarr integration

| Feature | What it does |
| --- | --- |
| Test | Pings `/api/v2/status` (with cruddb fallback for older Tdarr) |
| Sync | Pulls library list and Flow / plugin list (via `cruddb` collection scan) |
| List libraries | UI populates dropdown for "Library mode" automation rules |
| List plugins | UI populates dropdown for "Plugin mode" automation rules |
| Queue transcode | Three modes: library, plugin/flow, or inline profile (codec / container / CRF / HWA) |
| Path mappings | `[{local: '/host/media', remote: '/data'}]` longest-prefix-match translation |
| Webhook | Receives Tdarr completion events when wired with the community webhook flow |
| Automation | Rules can `transcode_via_tdarr` with a profile spec when files exceed a severity threshold |

### Inline profile fields

```json
{
  "codec":          "hevc | h264 | av1",
  "container":      "mkv | mp4",
  "audio_codec":    "copy | aac | ac3 | eac3",
  "audio_bitrate":  "128k",
  "video_bitrate":  "5M",
  "crf":            22,
  "hardware_accel": "qsv | nvenc | vaapi | null",
  "resolution_max": "1080p | 720p | null"
}
```

## Severity scale

| Severity | Meaning |
| --- | --- |
| **Unplayable** | File has issues or formats Plex/Jellyfin can't play |
| **Always Transcode** | Will always transcode (Chrome web client baseline) |
| **Possible Transcode** | Some clients won't direct-play |
| **High Bitrate** | Above your configured threshold (default 80 Mbps) |
| **Info** | Worth noting but generally fine |
| **OK** | Direct-plays on most clients |

## Dashboard

The dashboard now has Media as the central panel with a hero summary (score + status + quick stats), a clickable severity-tile grid, and four clickable bar panels (video codecs, audio codecs, resolutions, issue categories). The side panel shows non-media categories (Subtitles / Images / Metadata / Junk) as compact cards each with their own severity pills. **Every number on the dashboard is clickable** and jumps to the file browser with the matching filter pre-applied.

Ignored files (matching any of your `ignore_patterns`) are never stored in the database, so they don't appear in any category, count, or breakdown.

## Compatibility modes

Settings → Compatibility Mode:

- **Plex only** — 17 Plex devices in the matrix
- **Jellyfin only** — 11 Jellyfin clients (Web, JMP, Android, Roku, Swiftfin, Kodi, Infuse…)
- **Both** — 28 devices grouped by ecosystem

Jellyfin overrides reflect real-world differences from Plex — e.g. Jellyfin Media Player (mpv-based) handles HEVC 10-bit, ASS subtitles and TrueHD natively; DV passthrough is broken on most Jellyfin clients except Infuse.

## File categories

- **Media** — `.mkv`, `.mp4`, `.avi`, `.mov`, `.ts`, `.m2ts`, `.webm`, etc.
- **Subtitle** — `.srt`, `.ass`, `.ssa`, `.sub`, `.vtt`, `.idx`, `.sup`, `.smi`
- **Image** — `.jpg`, `.png`, `.webp`
- **Metadata** — `.nfo`, `.xml`, `.txt`, `.sfv`
- **Junk** — anything else

Files matching any pattern in `ignore_patterns` are skipped entirely.

### Ignore patterns now support globs

| Pattern | Matches |
| --- | --- |
| `.plexmatch` | Exact filename |
| `Thumbs.db` | Exact filename |
| `*.tmp` | Any file ending in `.tmp` |
| `*.partial` | Any file ending in `.partial` |
| `_UNPACK_*` | Any file in a directory starting with `_UNPACK_` |
| `@eaDir` | Any file inside an `@eaDir` folder (component match) |

Newly-added patterns are applied during the next *Full Scan* — files that newly match are removed from the database (in addition to files that no longer exist on disk).

## Three scan modes

| Mode | Speed | Use when |
| --- | --- | --- |
| **Run Full Scan** | minutes/hours | First scan, or library has changed substantially |
| **⚡ Re-eval Rules** | seconds | Rules updated, threshold changed — applies new logic to stored ffprobe data without re-reading files |
| **Targeted Scan** | per-file | Re-probe specific files; webhook handler uses this automatically |

## Project structure

```
auditarr/
├── server.py                  Flask routes + scheduler + auth middleware
├── auth.py                    PBKDF2 single-user auth + API token + sessions
├── updater.py                 GitHub commit poller
├── db.py                      SQLite schema + custom rule SQL evaluator
├── checks.py                  Built-in rules + Plex/Jellyfin device map + glob ignore
├── scanner.py                 Scan orchestration with custom rule application
├── integrations/
│   ├── __init__.py            Registry, polling worker, automation runner
│   ├── base.py                Integration ABC
│   ├── sonarr_radarr.py       Full Sonarr + Radarr (existing)
│   ├── bazarr.py              Full Bazarr (sync, webhook, delete/search subs)
│   ├── tdarr.py               Full Tdarr (libraries, plugins, queue, path mapping)
│   └── scaffolds.py           Plex / Jellyfin stubs
├── frontend/
│   ├── index.html             Main UI shell
│   ├── login.html             First-run setup + login page
│   └── app.js                 Frontend logic
├── config.json                User config (auto-created)
├── auth.json                  Single-user credentials (mode 0600, auto-created)
├── .auditarr_version.json     Tracked SHA for the update checker
└── media_audit.db             SQLite store (auto-created)
```

## REST API

All endpoints under `/api/*` require authentication (cookie session or API token) **except**:

- `GET /api/auth/status`
- `POST /api/auth/setup` (only callable when not yet configured)
- `POST /api/auth/login`
- `POST /api/integrations/webhook/<id>` (third-party services)

```
─── Auth ───
GET    /api/auth/status
POST   /api/auth/setup                  (first-run only)
POST   /api/auth/login
POST   /api/auth/logout
POST   /api/auth/change-password
GET    /api/auth/api-token
POST   /api/auth/api-token              (regenerate)

─── Updates ───
GET    /api/update/check
POST   /api/update/refresh
POST   /api/update/mark-current

─── Config ───
GET    /api/config
POST   /api/config

─── Scan ───
POST   /api/scan/start
POST   /api/scan/reeval
POST   /api/scan/targeted
GET    /api/scan/<job_id>/status

─── Files ───
GET    /api/files?file_category=&severity=&q=&codec=
GET    /api/files/<id>                  (compat-mode-filtered device matrix)
POST   /api/files/<id>/{rescan,delete,rename,move,monitor,virustotal}

─── Stats / reference ───
GET    /api/stats                       (per-category breakdown, no 'ignored')
GET    /api/devices
GET    /api/severities

─── Integrations ───
GET    /api/integrations/plugins
GET    /api/integrations
POST   /api/integrations
PUT    /api/integrations/<id>
DELETE /api/integrations/<id>
POST   /api/integrations/<id>/test
POST   /api/integrations/<id>/sync
POST   /api/integrations/webhook/<id>   (PUBLIC — for Sonarr/Radarr/Bazarr)
GET    /api/integrations/events

─── Bazarr actions ───
POST   /api/bazarr/<id>/delete-sub
POST   /api/bazarr/<id>/search-subs

─── Tdarr actions ───
GET    /api/tdarr/<id>/libraries
GET    /api/tdarr/<id>/plugins
GET    /api/tdarr/<id>/jobs
POST   /api/tdarr/<id>/queue            (file_id|path + library_id|plugin_id|inline_profile)

─── Automation ───
GET    /api/automation/rules
POST   /api/automation/rules            (action_config + file_category supported)
PUT    /api/automation/rules/<id>
DELETE /api/automation/rules/<id>
POST   /api/automation/run

─── Custom rules ───
GET    /api/rules/schema
GET    /api/rules
GET    /api/rules/<id>
POST   /api/rules
PUT    /api/rules/<id>
DELETE /api/rules/<id>
POST   /api/rules/test
GET    /api/rules/<id>/preview
POST   /api/rules/apply
```

### Example: trigger a Tdarr transcode via API

```bash
curl -X POST http://localhost:7842/api/tdarr/2/queue \
  -H "X-API-Key: $AUDITARR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "path": "/host/media/Movies/Big.Movie.2024.mkv",
    "inline_profile": {
      "codec": "hevc",
      "container": "mkv",
      "crf": 22,
      "audio_codec": "copy",
      "hardware_accel": "qsv"
    }
  }'
```

The path is run through your Tdarr integration's `path_mappings` before being sent to Tdarr.
