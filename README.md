# Auditarr

A web-based media library auditor for Plex/Jellyfin/Sonarr/Radarr setups. Scans your library, stores everything in SQLite, evaluates against a comprehensive compatibility ruleset (with custom rules support), and lets you act on the findings.

## Run

```bash
pip install flask apscheduler
python3 server.py
# → http://localhost:7842
```

Requires `ffprobe` (from `ffmpeg`) on PATH. Tested on Python 3.10+.

## What's new in v4

- **Renamed** to Auditarr
- **Custom rules** — define your own severity rules from any field gathered during scan (codec, bitrate, size, container, etc.). Both visual builder and raw JSON editor.
- **Jellyfin compatibility** — Jellyfin device list (Web, Media Player, Android, Roku, Swiftfin, Kodi, Infuse, etc.) added alongside Plex. Toggleable: Plex-only / Jellyfin-only / Both.
- **Per-category dashboard** — separate Media / Subtitles / Junk health views via tabs, each with its own score and severity breakdown
- **Performance** — file list capped at 500 visible rows with debounced search; auto-picks first non-empty category
- **Plugin picker race fixed** — Sonarr/Radarr always selectable
- **Re-eval clarity** — toast confirms "no rescan" when re-applying rules

## Severity scale

| Severity | Meaning |
|---|---|
| **Unplayable** | File has issues or formats Plex/Jellyfin can't play |
| **Always Transcode** | Will always transcode (Chrome web client baseline) |
| **Possible Transcode** | Some clients won't direct-play |
| **High Bitrate** | Above your configured threshold (default 80 Mbps) |
| **Info** | Worth noting but generally fine |
| **OK** | Direct-plays on most clients |

A file's headline severity is the **worst** across all its issues.

## Custom rules

Tag any file with any severity based on its metadata:

- **Visual builder**: pick a field (codec, bitrate, size, container, extension, …), an operator (=, ≠, >, contains, in, …), and a value. Add multiple conditions joined by ALL or ANY.
- **JSON editor**: write/paste raw spec JSON (`{ match: 'all', conditions: [{field, op, value}] }`).
- **Test before saving**: see exactly which files match.
- **Apply Rules Only**: skip a full re-eval — apply customs to existing files only.

Rules run automatically on every scan and re-evaluation. Disabled rules are skipped.

Available fields:
`extension · category · codec · audio_codec · container · resolution · dovi_profile · size_bytes · size_mb · size_gb · bitrate · bitrate_mbps · duration_sec · name · path · scan_status · monitored · arr_kind`

Available operators:
`eq · neq · gt · gte · lt · lte · contains · starts_with · ends_with · in · is_null · not_null`

## Compatibility modes

Settings → Compatibility Mode:

- **Plex only** — 17 Plex devices in the matrix
- **Jellyfin only** — 11 Jellyfin clients (Web, JMP, Android, Roku, Swiftfin, Kodi, Infuse…)
- **Both** — 28 devices grouped by ecosystem

Jellyfin overrides reflect real-world differences from Plex — e.g. Jellyfin Media Player (mpv-based) handles HEVC 10-bit, ASS subtitles and TrueHD natively where many Plex clients transcode; conversely DV passthrough is broken on most Jellyfin clients except Infuse.

## File categories

- **Media** — `.mkv`, `.mp4`, `.avi`, `.mov`, `.ts`, `.m2ts`, `.webm`, etc.
- **Subtitle** — `.srt`, `.ass`, `.ssa`, `.sub`, `.vtt`, `.idx`, `.sup`, `.smi`, etc.
- **Image** — `.jpg`, `.png`, `.webp`, etc.
- **Metadata** — `.nfo`, `.xml`, `.txt`, `.sfv`, etc.
- **Junk** — anything else

Files matching the **ignore patterns** list are skipped entirely (not even recorded).

## Three scan modes

| Mode | Speed | Use when |
|---|---|---|
| **Run Full Scan** | minutes/hours | First scan, or library has changed substantially |
| **⚡ Re-eval Rules** | seconds | Rules updated, threshold changed — applies new logic to stored ffprobe data without re-reading files |
| **Targeted Scan** | per-file | Re-probe specific files; webhook handler uses this automatically |

## Subtitle validation

External subtitle files are checked for:

- Readability with common encodings (UTF-8, UTF-8 BOM, CP1252, Latin-1)
- Format-specific structure (SRT timecode arrows, VTT WEBVTT header, ASS sections, etc.)
- A matching media file with the same base name in the same folder
- A 2- or 3-letter language tag in the filename

## Integrations

| Plugin | Test | Sync | Webhook | Automation |
|---|---|---|---|---|
| **Sonarr** | ✓ | ✓ | ✓ | ✓ |
| **Radarr** | ✓ | ✓ | ✓ | ✓ |
| **Plex** | ✓ | — | — | — |
| **Jellyfin** | ✓ | — | — | — |
| **Tdarr** | ✓ | — | — | — |
| **Bazarr** | ✓ | — | — | — |

## Automation rules

Auto-toggle Sonarr/Radarr monitoring based on file severity:

> *When file severity \[at\_least | at\_most | equals\] X, then \[monitor | unmonitor\] in \<integration\>*

Rules run after every scan/re-evaluation and after manual triggers.

## Project structure

```
auditarr/
├── server.py                  Flask routes + scheduler
├── db.py                      SQLite schema + custom rule SQL evaluator
├── checks.py                  Built-in rules + Plex/Jellyfin device map
├── scanner.py                 Scan orchestration with custom rule application
├── integrations/
│   ├── __init__.py            Registry, polling worker, automation runner
│   ├── base.py                Integration ABC
│   ├── sonarr_radarr.py       Full Sonarr + Radarr
│   └── scaffolds.py           Plex / Jellyfin / Tdarr / Bazarr stubs
├── frontend/
│   ├── index.html             UI shell (B&W sleek)
│   └── app.js                 Frontend logic
├── config.json                User config (auto-created)
└── media_audit.db             SQLite store (auto-created)
```

## REST API (selected)

```
GET/POST /api/config

POST /api/scan/{start,reeval,targeted}
GET  /api/scan/<job>/status

GET  /api/files?file_category=&severity=&q=
GET  /api/files/<id>                            (compat-mode-filtered device matrix)
POST /api/files/<id>/{rescan,delete,rename,move,monitor,virustotal}

GET  /api/stats                                 (per-category breakdown)
GET  /api/devices                               (plex/jellyfin/all + ecosystem map)

GET  /api/integrations/plugins
GET/POST/PUT/DELETE /api/integrations[/<id>]
POST /api/integrations/<id>/{test,sync}
POST /api/integrations/webhook/<id>

GET/POST/PUT/DELETE /api/automation/rules[/<id>]
POST /api/automation/run

GET  /api/rules/schema                          (visual builder field+op catalog)
GET  /api/rules
GET  /api/rules/<id>
POST /api/rules                                 (create custom rule)
PUT  /api/rules/<id>
DELETE /api/rules/<id>
POST /api/rules/test                            (test spec without saving)
GET  /api/rules/<id>/preview
POST /api/rules/apply                           (apply rules without full re-eval)
```
