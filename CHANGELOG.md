# Changelog

All notable changes to Auditarr. Dates reflect the day the stage was
shipped from the workspace.

## [1.8.2] — 2026-05-17 — Updater wiring fixes for bare-metal install

Patch release fixing four overlapping defects that prevented the
"Check for updates" → "Apply update" flow from working on a fresh
bare-metal install, even after the operator correctly set the feed
URL to `https://api.github.com/repos/ShogyX/Auditarr/releases/latest`
in the UI.

### What was wrong

1. **`Settings.app_version` defaulted to `"1.6.0"` and never drifted
   forward.** Every release since v1.7.0 bumped `app.__version__`
   but the `Settings` field stayed at 1.6.0. The updater compared
   feed responses against this stale value, so it always reported
   "update available" for any release ≥ 1.6.0 AND wrote a stale
   `from_version` into the apply sentinel. Operators who clicked
   "Check now" with the latest version already installed would see
   a phantom update available.
2. **Default `update_feed_url` pointed at the wrong repo.**
   `app/core/settings.py` and `app/core/runtime_settings_schema.py`
   both defaulted to `https://api.github.com/repos/auditarr/auditarr/releases/latest`
   — a non-existent repo. Fresh installs hit a 404 on the first
   check until the operator overrode it via the UI.
3. **Bare-metal updater script required an explicit
   `AUDITARR_RELEASE_TARBALL_URL`** to do anything. The watcher
   was opt-in by design, but the only example URL in the
   installer-written `updater.env` also pointed at the wrong repo,
   so operators who uncommented it would still 404 on download.
4. **The watcher didn't see the feed URL.** Even if the operator
   set `AUDITARR_UPDATE_FEED_URL` correctly in `auditarr.env`, the
   `auditarr-update-watcher.service` unit only sourced `updater.env`,
   so the watcher had no way to derive a tarball URL from the
   feed URL.

### Fixes

**Backend:**

- `Settings.app_version` now uses `default_factory=lambda: _default_app_version()`
  which imports `from app import __version__`. The default automatically
  tracks the package version on every release. Operators can still override
  via `AUDITARR_APP_VERSION` for non-standard deployment tooling
  (staging builds with git-SHA suffixes, etc.).
- Default `update_feed_url` in `settings.py` AND the runtime
  settings schema now points at the correct upstream repo:
  `https://api.github.com/repos/ShogyX/Auditarr/releases/latest`.

**Bare-metal updater script (`updater/auditarr-update-bare-metal.sh`):**

- Auto-derives `RELEASE_URL_TEMPLATE` from `UPDATE_FEED_URL` when
  the operator hasn't set one explicitly. The regex
  `^https://api\.github\.com/repos/([^/]+)/([^/]+)/releases/latest$`
  matches against the feed URL and produces a GitHub
  source-tarball URL of the form
  `https://github.com/<owner>/<repo>/archive/refs/tags/v%s.tar.gz`.
  Operators with a private mirror or non-GitHub feed can still
  override `AUDITARR_RELEASE_TARBALL_URL` explicitly.
- Failure message updated to name the new behaviour: if the feed
  URL isn't a GitHub `releases/latest` URL AND no explicit tarball
  URL was set, the apply fails with clear recovery instructions
  pointing at both env vars.

**Bare-metal installer (`install-bare-metal.sh`):**

- Writes `AUDITARR_UPDATE_FEED_URL=https://api.github.com/repos/ShogyX/Auditarr/releases/latest`
  to the generated `auditarr.env` so the watcher sees it (rather
  than relying on the backend default).
- `auditarr-update-watcher.service` unit now has a second
  `EnvironmentFile=-$APP_CONFIG_DIR/auditarr.env` directive
  (the leading `-` marks it optional). The watcher process now
  sees both `updater.env` AND the main app env file, so
  `AUDITARR_UPDATE_FEED_URL` reaches the derivation logic.
- `updater.env` template updated: comment block describes v1.8.2's
  feed-URL-derived default and the example URLs point at
  `ShogyX/Auditarr` instead of the non-existent `auditarr/auditarr`.

### Test counts

  * Backend unit: **552/552** (+3 settings tests pinning `app_version`
    factory + ShogyX/Auditarr feed URL default)
  * Backend integration updater: 12/12 (unchanged)
  * Backend integration full (scan + reaper + playback + session manager): 48/48
  * Backend e2e: 4/4 (with bumped 1.8.2 pin)
  * Frontend: 432/432 (unchanged)
  * Smoke source-tree: 8/8 with 1.8.2 stamps
  * Ruff F-rules: clean
  * **NEW: bash unit tests for the watcher's URL derivation:**
    `updater/tests/test_url_derivation.sh` — 10/10 covering
    happy path, edge cases (trailing slash, http vs https,
    hyphenated repo names), and the explicit-template-wins
    override.

### Upgrade from v1.8.1

No DB migration needed. The bare-metal install path benefits from
a fresh `auditarr.env` regeneration; the installer's auditarr.env
heredoc is idempotent (regenerated each run, with `SECRET_KEY`
preserved from an existing file).

```bash
sudo systemctl stop auditarr-api auditarr-worker auditarr-update-watcher
# Extract v1.8.2 over /opt/auditarr
sudo bash /opt/auditarr/install-bare-metal.sh   # regenerates env files
sudo systemctl start auditarr-api auditarr-worker auditarr-update-watcher
sudo journalctl -u auditarr-update-watcher -f
```

You should see at watcher startup:

```
[auditarr-update] <ts> watching /var/lib/auditarr/updater/apply.request (interval=5s)
[auditarr-update] <ts> release URL template: <not configured>
```

The "not configured" is fine — the watcher derives the URL on
demand when an apply request comes in. Click "Apply update" in the
UI; you should then see:

```
[auditarr-update] <ts> apply requested: id=... to=1.8.3
[auditarr-update] <ts> derived release URL template: https://github.com/ShogyX/Auditarr/archive/refs/tags/v%s.tar.gz
[auditarr-update] <ts> fetching https://github.com/ShogyX/Auditarr/archive/refs/tags/v1.8.3.tar.gz
```

### Diagnostic methodology

For the next operator hitting "Apply update does nothing on bare-metal":

1. **Check `journalctl -u auditarr-update-watcher | grep "release URL template"`.**
   On a healthy install you'll see either an explicit
   `AUDITARR_RELEASE_TARBALL_URL` value or `<not configured>` (the
   watcher derives lazily). If the line is missing, the watcher
   isn't running.
2. **Check the systemd unit's `EnvironmentFile=` directives:**
   `systemctl cat auditarr-update-watcher | grep EnvironmentFile`.
   v1.8.2 expects two lines: `EnvironmentFile=/etc/auditarr/updater.env`
   AND `EnvironmentFile=-/etc/auditarr/auditarr.env`. If only one
   is present, re-run `install-bare-metal.sh` to regenerate.
3. **Verify the feed URL reaches the watcher:**
   `systemctl show auditarr-update-watcher --property=Environment | grep AUDITARR_UPDATE_FEED_URL`.
   If this is empty, `auditarr.env` doesn't have the var set, or
   the second `EnvironmentFile=` directive is missing from the unit.
4. **Click "Check now"** and inspect the `UpdateCheck` row via
   `GET /api/v1/updater/checks`. `ok=true` and a non-null
   `latest_version` confirms the feed pathway is working
   end-to-end.

### Backwards compatibility

- No DB schema changes.
- `Settings.app_version` factory: explicit override via
  `AUDITARR_APP_VERSION` still works, so any tooling that pinned a
  version env var is unaffected.
- Watcher script's explicit `AUDITARR_RELEASE_TARBALL_URL` still
  takes precedence over the new derivation; existing private-mirror
  configurations keep working.
- Installer-generated `updater.env` is only written when the file
  doesn't exist, so existing installs keep their config. The new
  `AUDITARR_UPDATE_FEED_URL` line is appended only on fresh
  installs OR an idempotent re-run that detects the absence of the
  key (caveat: the current installer regenerates the full
  `auditarr.env` heredoc — operators who hand-edited that file
  should diff their changes back in after re-running).

---

## [1.8.1] — 2026-05-17 — Scan trigger reliability

Patch release fixing the "sometimes works, sometimes doesn't, no
error shown" symptom on the Run Scan / Scan all libraries buttons.

### What was wrong

Four overlapping defects in the scan-trigger flow:

1. **Stale `running`/`queued` ScanRun rows blocked all future
   scans.** The `POST /scans/libraries/{id}` endpoint refuses
   to start a scan if there's already a `queued` or `running`
   row for the library. If the worker process was killed
   mid-scan (OOM, SIGKILL from systemd, container restart, host
   reboot), the row never transitioned to `failed` — Python
   exception handlers don't run on a hard kill. The row sat at
   `running` forever, blocking every subsequent click with a
   409 the frontend swallowed silently.

2. **ARQ silently dropped duplicate enqueues.** The API called
   `redis.enqueue("scan_library", library.id, ...)` without
   `_job_id`. ARQ generates a deterministic hash of
   `(function, args, kwargs)` for dedup; two scans of the same
   library with the same mode would collide, and the second
   `enqueue_job()` returned `None`. The API never checked the
   return value, so the row was committed as `queued` but the
   worker never picked it up.

3. **Frontend had zero error handling.** `useTriggerScan` and
   `useTriggerScanAll` had `onSuccess` but no `onError`. The
   409 / 5xx messages never reached the user. The button just
   stopped being "Scanning…" and the operator was left guessing.

4. **No success feedback.** A 202 with the queued ScanRun row
   produced a brief "Scanning…" state but no positive
   confirmation. Combined with #3, operators couldn't tell
   whether their click did anything.

### Fixes

**Backend:**

- **New `reap_stale_scans` worker cron** — runs every 5 minutes,
  marks any `queued`/`running` row whose `started_at` (or
  `created_at` for never-started queued rows) is older than 1
  hour as `failed` with a diagnostic message
  ("Reaped by stale-scan watchdog: row was stuck at 'running'
  for N seconds…"). Also flips the library's
  `last_scan_status` and emits a `scan.reaped` event so WS
  subscribers refresh.
- **Explicit `_job_id` on ARQ enqueue** — passes
  `_job_id=f"scan_library:{run.id}"` so each call gets a
  unique dedup key, AND checks the return value. On `None`
  (collision with a stale job), marks the row failed with a
  clear "ARQ refused to enqueue" message instead of leaving
  it stuck.
- **New `POST /scans/libraries/{id}/reset` admin endpoint**
  — forcibly marks any stuck `queued`/`running` rows for a
  library as `failed`. Operators who don't want to wait the
  full hour for the reaper can use this immediately.

**Frontend:**

- **`onSuccess` and `onError` toasts** on `useTriggerScan` and
  `useTriggerScanAll`. Success toasts confirm "Scan queued".
  Errors toast a useful message; 409 surfaces a "Use 'Unstick
  library' to clear it if it's stuck" hint; 403 names the
  admin-permission requirement.
- **`useResetLibraryScans` hook** wrapping the new reset
  endpoint with its own success/error toasts.
- **`FilesScanErrorBanner` on the Files page** — renders when
  the most recent `useTriggerScan` error was a 409 with the
  current library, with an "Unstick library" button calling
  the reset endpoint. Dismissable, replaces itself the next
  time the trigger fails.

### Test counts

- Backend unit: **549/549** (unchanged)
- Backend integration:
  - Scan reaper + reset: **10/10** (new)
  - Stage 8 scan tests: 18/18 (unchanged)
- Backend e2e: 4/4 (with 1.8.1 version pin)
- Frontend: **432/432** (+7 banner tests)
- Smoke source-tree: 8/8 with 1.8.1 stamps

### Diagnostic methodology

For the next operator hitting "scan does nothing":

1. **Check the worker log for `worker.reap_stale_scans.reaped`** —
   if you see it within 5 minutes of restart, you had stuck rows;
   the reaper just cleaned them up. Click Run Scan again and it
   should work.
2. **Check for 409 in the network panel** when clicking Run Scan
   — that means a stuck row was present at click time. The new
   banner offers an "Unstick library" button; in 1.8.0 you'd
   have to wait for the reaper or restart the worker.
3. **Check `scans.enqueue_dedup_collision`** in the worker log
   — that's the new diagnostic for the ARQ-collision case.
   Should be rare with v1.8.1's unique `_job_id`, but the log
   line will name the run_id so you know which row.

### Upgrade from v1.8.0

No migration needed (additive code only).

```bash
sudo systemctl stop auditarr-api auditarr-worker
# Extract v1.8.1 over /opt/auditarr
sudo -u $APP_USER /opt/auditarr/venv/bin/pip install -e /opt/auditarr/backend
sudo systemctl start auditarr-api auditarr-worker
# Verify within 5 minutes:
sudo journalctl -u auditarr-worker | grep "reap_stale_scans" | tail -5
```

If your worker had stuck scans from prior crashes, the first
reaper tick (at startup) will unstick them. Your Run Scan
button starts working again immediately.

### Backwards compatibility

- No DB migration. Schema unchanged.
- New `POST /scans/libraries/{id}/reset` endpoint; existing
  endpoints unchanged.
- New `_job_id` parameter on ARQ enqueue is purely additive.
- Frontend `useResetLibraryScans` is a new hook; existing
  `useTriggerScan` / `useTriggerScanAll` signatures unchanged
  (only behaviour was added).

---

## [1.8.0] — 2026-05-17 — SSE-based Plex session tracking

Architectural rework of Plex live-session monitoring. Diagnosed via
the working Tracearr reference repo
(https://github.com/connorgallopo/Tracearr) after v1.7.2 fixed the
SSL bundle issue but the underlying "first 2 sessions captured, rest
not" symptom persisted.

### Why polling didn't work

The Stage 09 (v1.7) live tile polled `GET /status/sessions` every
15 seconds. Two design flaws made this inherently lossy:

1. **`/status/sessions` is a snapshot, not an event log.** Sessions
   that start AND end within a 15-second poll window are invisible.
   Sessions that span multiple polls show up. That exactly matches
   the production symptom: long sessions captured, short ones
   missed.
2. **`/status/sessions/history/all` only contains "watched" plays.**
   Plex's history table only records sessions that crossed the
   watched threshold (configurable per-library, default ~90% of
   duration). Aborted, scrubbed, or sub-threshold sessions never
   enter Plex's history, so they never reached our `playback_events`
   table no matter how often the worker polled.

Neither flaw is fixable by patching the parser. The fix is the
architecture: subscribe to Plex's real-time event stream and
record session lifecycle ourselves.

### What's new in v1.8.0

**Plex SSE listener.** Plex Media Server exposes
`GET /:/eventsource/notifications` — a long-running Server-Sent
Events endpoint that pushes JSON-encoded events (playing, paused,
stopped, transcode progress) as activity happens on the server.
The v1.8.0 worker establishes one persistent SSE connection per
enabled Plex integration and records every session lifecycle
event.

  * **New `app.core.sse`** — async SSE client with W3C spec
    compliant block parsing, exponential reconnect backoff
    (1s, 2s, 4s, 8s, 16s, 30s), and a synthetic `RECONNECTING`
    event so subscribers can re-sync state after reconnect.
  * **`PlexProvider.subscribe_sessions()`** — wraps the SSE
    client, parses Plex's `NotificationContainer` envelope, and
    yields `PlexSessionEvent` dataclasses.
  * **`PlexProvider.fetch_one_session_snapshot()`** — enriches an
    SSE event with the full `/status/sessions` metadata
    (codec, user, device, path) since Plex's SSE payload is
    thin (just sessionKey, state, ratingKey, viewOffset).

**New `playback_sessions` table.** Mutable lifecycle row per
session. Schema:

  * `(integration_id, session_key)` unique constraint —
    one row per upstream session.
  * `state` — "playing" / "paused" / "buffering" / "stopped"
  * `decision`, `reason_code` — captured at start; updated on
    transcoder-decision-change events.
  * Source + target stream details: codec, bitrate, resolution,
    container.
  * `view_offset_ms`, `duration_ms` — refreshed on every event
    so the live tile shows accurate progress.
  * `started_at`, `last_event_at`, `stopped_at` — lifecycle
    timestamps recorded by us, not scraped from Plex's history.
  * `reconciled_with_history` — flipped to TRUE when the
    existing history scrape later observes the same session,
    so the analyzer doesn't double-count.

Migration `0027_stage17_playback_sessions` is purely additive
(new table + index). Existing `playback_events` data is
untouched.

**`SessionStateManager`** — owns the SSE → DB write path. Uses
a query-first INSERT-or-UPDATE pattern (not an ON CONFLICT
upsert) so a stop event without enrichment doesn't blank the
metadata we captured at start. Idempotent — replaying the same
event produces the same row, which matters because Plex retries
SSE events on reconnect.

**Worker supervisor** (`app.worker_sse`). At startup queries
enabled Plex integrations and spawns one long-running listener
task per. Each task is supervised: a permanent error (4xx auth)
stops the listener; a transient error (transport blip, 5xx)
restarts with exponential backoff (5s, 15s, 60s, 300s). Tasks
are cancelled cleanly at worker shutdown.

**Live endpoint rewrite.** `GET /api/v1/playback/live` now
reads Plex sessions from the `playback_sessions` table —
~50ms DB query instead of a per-request upstream HTTP call.
Sessions appear within milliseconds of start because the SSE
listener writes them as Plex pushes the event. Jellyfin
sessions continue to use the polling fallback path
(`fetch_live_playbacks`) because Jellyfin doesn't expose SSE —
its WebSocket equivalent is planned for v1.8.x.

**History reconciliation.** The existing
`PlaybackPoller.poll_one` (15-minute history scrape) still
runs. When it observes an event whose `(integration_id,
started_at±60s)` matches an existing `playback_sessions` row,
it flips `reconciled_with_history=True`. The analyzer queries
both tables and dedupes on the flag.

### What's NOT in v1.8.0

  * **No frontend changes.** The dashboard's live tile reads
    the same JSON shape from `/api/v1/playback/live` — the
    fields haven't changed. The tile will now show short
    sessions that the v1.7 polling path missed.
  * **Jellyfin still polls.** v1.8.x will add Jellyfin
    WebSocket session monitoring.
  * **No UI for `playback_sessions` history.** The new table
    captures every session including aborted ones, but the
    history page still reads `playback_events`. A future
    release will surface the richer history.

### Test counts

  * Backend unit: **549/549** (+11 SSE tests; was 538)
  * Backend integration playback: **42/42** (+7 session
    manager + +1 stopped-sessions-excluded; was 34)
  * Backend integration plugin lifecycle: 50/50
  * Backend integration VT: 38/38
  * Backend e2e: 4/4 (with 1.8.0 version pin)
  * Migration chain test: green at head `0027_stage17_playback_sessions`

### Upgrade from v1.7.2

```bash
sudo systemctl stop auditarr-api auditarr-worker
# Replace /opt/auditarr with the v1.8.0 extracted tree
sudo -u $APP_USER /opt/auditarr/venv/bin/pip install -e /opt/auditarr/backend
sudo -u $APP_USER /opt/auditarr/venv/bin/alembic -c /opt/auditarr/backend/alembic.ini upgrade head
sudo systemctl start auditarr-api auditarr-worker
# Within ~5 seconds the worker should log:
sudo journalctl -u auditarr-worker -f | grep -E "sse\.|worker\.sse\."
```

Expected sequence within seconds of restart:
```
worker.sse.listener_starting  integration_id=...  integration_name=My Plex Server
sse.connected                  url=...:/eventsource/notifications  status=200
worker.plex_listeners_spawned  count=1
worker.started
```

Then as soon as someone hits play in any Plex client:
```
session_manager.event_recorded  state=playing  view_offset_ms=N  enriched=True
```

The next dashboard render shows the session.

### Backwards compatibility

  * `playback_events` schema unchanged — old data preserved.
  * `playback_sessions` is additive — no existing code depends
    on it before v1.8.0.
  * Integration provider Protocol gains `subscribe_sessions`
    and `fetch_one_session_snapshot` but both are optional
    (Jellyfin / Sonarr / Radarr / etc don't implement them).
  * No frontend changes; live tile JSON shape is identical.
  * Worker config unchanged. The SSE listeners spawn at startup
    and are cancelled at shutdown.

---

## [1.7.2] — 2026-05-17 — SSL bundle + integration bugfixes

Patch release addressing a cluster of production-reported bugs
diagnosed from a 503k-line log forensics pass.

### Root cause (Plex playback still broken in 1.7.1)

The v1.7.1 wire-shape fix to `fetch_playback_events` was correct
but irrelevant on the affected host. **Every** outbound HTTPS call
from the worker process was dying at `httpx.AsyncClient` construction
with `FileNotFoundError: [Errno 2] No such file or directory`. The
traceback chain proved this was `ssl.create_default_context` →
`load_verify_locations` failing because no CA bundle was reachable.

Root cause: the operator's venv had no `certifi` installed
(missing transitive dependency surfaced under Python 3.12 + httpx
≥ 0.27 strict-CA defaults), AND the host's `/etc/ssl/certs/
ca-certificates.crt` was present but httpx wasn't pointed at it.
So the worker's healthchecks AND playback polls all silently
failed before any HTTP traffic left the process.

### Fixes

1. **New `app.core.ssl_bundle` resolver.** Three-tier fallback:
   `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` / `CURL_CA_BUNDLE`
   env vars → `certifi.where()` → an OS bundle candidate list
   (Debian/Ubuntu, RHEL/CentOS, Alpine, FreeBSD, macOS Homebrew).
   Resolution result cached process-wide. When nothing is
   found, raises `CABundleMissingError` with a diagnostic that
   names every path tried.

2. **New `app.core.http.async_client`** factory. Drop-in
   replacement for `httpx.AsyncClient(...)` that resolves the
   CA bundle once and passes it via `verify=`. On
   `CABundleMissingError`, falls back to `verify=False` with a
   loud `http.client_verify_disabled` warning. This trades
   strict TLS verification for keeping the application functional
   on misconfigured hosts — the operator MUST fix the bundle to
   restore verification.

3. **Every integration plugin retrofitted** to use the new
   factory: Plex, Jellyfin, Sonarr, Radarr, Bazarr, Tdarr,
   VirusTotal. Also retrofitted: the updater feed, secret
   testers, HTTP notification provider, plugin gallery.

4. **Startup SSL sanity check.** Both `app.main` and
   `app.worker` startup hooks call `startup_sanity_check()`
   so misconfiguration is loud at boot rather than at the
   first poll tick. Non-fatal: the app keeps serving requests
   with the verify=False fallback if no bundle is found.

5. **`certifi>=2024.0` added explicitly** to `backend/
   pyproject.toml` dependencies. It was a transitive dep of
   httpx; explicit declaration makes deployment tooling notice
   when it's missing.

### Other bugs fixed this release

6. **VirusTotal plugin failed to load** with `TypeError:
   Plugin.__init__() got an unexpected keyword argument
   'id'`. `Plugin(id=..., version=...)` was always wrong — the
   base class takes only `(context)`. Other plugins (Plex,
   Sonarr, etc.) had this right; VT diverged in an earlier
   refactor and no test caught it because the plugin loader
   was stubbed in tests.

7. **VirusTotal healthcheck 404'd** because the URL was
   `/users/<self>` literally — the docstring's angle-bracket
   placeholder leaked into the f-string. Real VT v3 endpoint
   is `/users/me`.

8. **Scanner crashed on filenames with non-UTF-8 bytes.**
   `os.walk` returns surrogateescape-substituted strings
   (codepoints `U+DC80..U+DCFF`) for un-decodable filenames;
   asyncpg refuses to bind these as VARCHAR with
   `UnicodeEncodeError`. Scanner now detects via
   `_contains_undecodable_bytes()` and skips them with a
   `scanner.skipped_bad_encoding` warning (capped at 10
   examples + a final total) so the operator can find and
   rename the offending files. The grep recipe to locate
   them is included in the summary log line.

### Diagnostic upgrade

When the next operator hits a similar issue, the logs will
now say `ssl.ca_bundle_unresolvable` with an enumerated
"paths tried" list and a quick-fix command for Debian/
Ubuntu, instead of opaque `[Errno 2] No such file or
directory`. The 1.7.1 silent-swallow log fix in
`/api/v1/playback/live` remains in place.

### Backward compatibility

Patch-release safe. No DB migration. No DTO shape changes.
Integration provider Protocol surface unchanged. Restart the
backend after upgrading; if SSL was already working on your
host the only visible change is one info-level
`ssl.ca_bundle_resolved` line at startup.

### Upgrading from 1.7.1

On the affected host (the one with `ModuleNotFoundError:
No module named 'certifi'`):

```bash
sudo systemctl stop auditarr-api auditarr-worker
sudo -u $APP_USER /opt/auditarr/venv/bin/pip install -e /opt/auditarr/backend
sudo systemctl start auditarr-api auditarr-worker
```

Then within ~15 seconds the worker log should show
`plex.playback.fetched count=N raw_count=N` instead of
`plex.playback.fetch_failed`.

---

## [1.7.1] — 2026-05-17 — Plex playback bugfix

Patch release. Both Plex playback surfaces (live-now dashboard card
and historical playback ingestion) were silently failing in 1.7.0
despite the integration healthcheck reporting green. Root cause was
two HTTP wire-format bugs in `plugins/plex/backend.py` plus a
diagnostic gap in the live aggregating endpoint that hid the
symptom.

### Bugs fixed

1. **`fetch_playback_events` — pagination sent as query params
   instead of headers.** Plex documents `X-Plex-Container-Start` /
   `Size` as HTTP headers; sending as query params is silently
   ignored on most PMS builds and falls back to the server's
   default page (typically 50 entries). Fixed: pagination now in
   headers, container size raised to 500.

2. **`fetch_playback_events` — `viewedAt>=` URL filter operator
   was URL-encoded.** httpx encodes `>` to `%3E` regardless of
   how the URL is constructed (we tested raw strings,
   `httpx.URL`, and `httpx.Request` — all encode). PMS's filter
   parser is inconsistent across versions about decoding-then-
   matching. Fixed: removed the URL filter, apply the cutoff in
   Python after parsing. Deterministic across every PMS version.

3. **`fetch_live_playbacks` and `/playback/live` aggregator —
   silent exception swallowing.** Both surfaces caught provider
   errors with no logging, so operators saw an empty live tile
   with no signal in the logs to debug from. Fixed: every
   degradation path now logs a structured warning with the
   integration id, error type, and (where relevant) the upstream
   content-type.

### New tests

`backend/tests/integration/test_plex_playback_wire_shape.py` —
six tests using `httpx.MockTransport` to capture and inspect the
actual outgoing HTTP request shape. The original Stage 09 / Stage
16 sweeps tested the parsers in isolation and stubbed the
provider at the Protocol level; neither approach exercised the
actual wire shape, which is how the bug shipped green. These
new tests close the gap.

### Diagnostic checklist for operators after applying

When you start a Plex session, within ~15 seconds the live
aggregator logs one of:

  * `plex.live.fetched count=N raw_count=N` — working.
  * `plex.live.fetch_failed error_type=...` — actionable upstream
    error.
  * `plex.live.fetch_parse_failed content_type=...` — proxy or
    content-negotiation issue.
  * `playback.live.provider_failed integration_id=...` — provider
    blew up entirely.

At the worker's next playback-poll tick (default 15min):
`plex.playback.fetched count=N raw_count=N
filtered_out_pre_cutoff=K cutoff_unix=T` per integration.

### Backward compatibility

The DTO shapes and downstream consumers are unchanged. Anyone
upgrading from 1.7.0 to 1.7.1 needs to do nothing except restart
the backend; the bugfix is wire-shape only.

---

## [1.7.0] — 2026-05-16

**v1.7.0 is the consolidated v1.7 release.** Sixteen-stage plan
executed against the v1.6.x line, closing every UI screenshot
issue from the bug report and every item in `fixes.txt`. The
version number resets to 1.7.0 because this is a major
user-facing release; prior internal cuts went up to 1.17.0
which was an internal-only series. v1.7.0 supersedes everything
through 1.17.x.

**Migration head**: `0026_stage12_must_change_pw`. Sixteen
migrations total across Stages 01–13; only Stage 05's
quarantine-column drop is destructive. See `docs/getting-started/
upgrade-to-v1.7.md` for the upgrade flow and rollback notes.

### Stage 01 — Installer rename, build hygiene

`install.sh` renamed to `install-docker.sh` (and the bare-metal
installer to `install-bare-metal.sh`) so the Docker and
bare-metal paths are unambiguously named. A stub `install.sh`
remains at the repo root that prints the new name and exits —
muscle-memory operators get a clear message, not a silent miss.
Build flags tightened (`set -euo pipefail` standard across all
shell entry points). README + docs updated to call the new
installer names.

### Stage 02 — Column resize state for Files + Rules tables

Persisted per-column widths across reloads via the existing
prefs-store pattern. Pointer drag commits the new width on
`pointerup`. Pinned by `RulesTable.resize.test.tsx` and the
Files-table resize harness.

### Stage 03 — Built-in Plex compatibility rule

New `default-rule:plex-compat` shipped at first boot. Matches
codecs that fail on most Plex clients (av1, vp9, prores,
truehd, dts-hd ma). Honest description per addendum A.6: "most
clients, not all" — `docs/rules/plex-compatibility.md` (added
in Stage 14) documents the per-client compatibility matrix.

### Stage 04 — Help context for the Files page

`/api/v1/docs/help/files.overview` now returns the Files-page
docs page (the key existed; the content was the gap). The
auto-pick effect on `HelpPage` resolves the contextual link to
the right doc.

### Stage 05 — Quarantine removal

Quarantine column dropped from `media_files`. The pre-v1.7
quarantine-view dropdown gone from the Files toolbar; the
rule action gone from the engine; the dispositions-on-extension
"malicious" path now sets severity=crit and applies a
`malicious-extension` tag instead. Migration deletes rows
with `quarantined=TRUE`; the count is logged at WARNING level
so operators see what was deleted. Documented in
`docs/getting-started/upgrade-to-v1.7.md`.

### Stage 06 — Rule engine rewrite

`severity`, `action`, `conditions` columns on `rules`.
`acknowledged_destructive` flag required for any rule with a
`delete` action — the engine refuses to evaluate destructive
rules without the operator's explicit acknowledgement (addendum
A.0.1). Notify action gains a `throttle` block to cap delivery
frequency. Visual rule builder surfaces the new fields.
Dry-run preview shows exactly which files a rule would flag
before save.

### Stage 07 — Optimization profile routing

New `routing_target` column with `in_process` default (matches
pre-v1.7 behaviour) and a `tdarr` option for routing transcodes
through Tdarr. The profile dialog gained a provider profile
picker that fetches `/integrations/{id}/transcode-profiles`
when routing target is `tdarr`. New `tag_scope` array column.

### Stage 08 — Provider profile picker

Stage 07's routing-target picker enriched: the picker surfaces
profile id + label and persists the selection on
`provider_metadata.provider_profile_id`. Clearing the picker
removes the field cleanly (no orphan keys).

### Stage 09 — Live-now playback card

New `LiveNowCard` reads the Plex/Jellyfin live-playback
endpoints and renders currently-playing sessions on the
dashboard. Path-mappings hint surfaces when sessions are
unresolved (addendum A.7). Resolved sessions deep-link to the
file detail drawer.

### Stage 10 — VirusTotal integration

New `vt_status` column on `media_files`; new `vt_queue` table.
Integration disabled by default; enable in Settings →
Integrations → VirusTotal with an API key. Sends file hashes
only — never file contents (cross-cutting privacy 4.4). Visual
rule builder gains `vt_status` as a field with enum.

### Stage 11 — Webhook HMAC bypass + IP/DNS whitelist

For webhooks where HMAC isn't possible (some upstream services
emit unsigned payloads), Stage 11 adds an opt-in bypass paired
with an IP/DNS source whitelist. The UI is loud about it —
warning banner whenever the bypass is enabled (cross-cutting
security 4.3). Default behaviour unchanged.

### Stage 12 — Forgot-password terminal OTP path

When email isn't configured, the forgot-password flow now
generates a 12-character OTP, prints it in a bordered banner
to the application log at WARNING level (and to stdout), and
flags the resulting reset token as `must_change_on_use=True`.
On confirmation, the user lands on `/change-password` and
their account is gated until the new password is set.
`/auth/email-configured` endpoint added so the frontend can
adapt its forgot-password copy.

### Stage 13 — Live UI refresh polish + dashboard card management

Three improvements bundled. (1) Invalidation audit closed two
gaps (`useChangePassword`, `useUpdateProfile`) and documented
nine legitimate skips. (2) Scan progress now survives
navigation — state moved to a central `scanProgressStore`; WS
subscription lifted to AppShell. (3) Dashboard cards can be
disabled, restored, swapped via a per-card overflow menu;
addendum B.10 migrate callback preserves existing operators'
layout.

### Stage 14 — Documentation overhaul

Three new doc pages: `docs/getting-started/upgrade-to-v1.7.md`
(addendum C.3 — quarantine data-loss + migration list +
rollback), `docs/rules/plex-compatibility.md` (addendum A.6 —
honest per-client framing), `docs/rules/ai-authoring.md` (plan
§630 — complete rule-JSON vocabulary + AI-prompt template +
mass-import format). Three existing pages updated to remove
stale v1.6 references.

### Stage 15 — Context-driven dropdowns

New `GET /api/v1/media/vocabulary` endpoint returns the
distinct codec / container / extension / tag values currently
in the indexed library; cached in-process for 60 seconds. The
rule builder's value-input and the optimization profile
dialog's tag-scope input gained `<datalist>`-backed
autocomplete driven by the vocabulary. Free-text input
survives — vocabulary is non-restricting per plan §668.
Automation surfaces already vocabulary-driven via the
pre-existing `useTagsCatalog` and `useLibraries`.

### Stage 16 — Release gate

Version bumped to 1.7.0 across `backend/app/__init__.py`,
`backend/pyproject.toml`, `frontend/package.json`, and
`install-bare-metal.sh`. CHANGELOG entry consolidates Stages
01–15. New integration smoke test (`backend/tests/e2e/
test_release_smoke_stage16.py`) walks register → admin grant
→ login → `/health`, `/docs`, `/media`, `/media/vocabulary`,
`/rules` against an in-memory app instance. New
`scripts/post-fix-smoke.sh` validates installer rename, no
`quarantine` references in shipped sources, VT integration
registered, optimization routing targets present, files-page
resize wiring present.

### Test counts at release

- Backend unit: 520/520
- Backend integration: 230+ across all slices
- Frontend full sweep: 425/425 across 68 files
- Migration chain: 26 migrations, head `0026_stage12_must_change_pw`
- Lint: ruff F-rules clean, eslint 0 warnings, tsc strict clean

### Breaking changes (vs v1.6.x)

- **Quarantine removed.** The `quarantined` column on
  `media_files` is dropped by migration 0021. Rows with
  `quarantined=TRUE` are deleted (count logged). The
  `quarantine` rule action is gone. See the upgrade doc.

### Non-breaking notable changes

- Installer renames (`install.sh` → `install-docker.sh`). The
  stub still exists for muscle-memory invocations.
- Frontend localStorage `auditarr.ui` schema bumped to
  `version: 1`. Existing state is migrated by the addendum
  B.10 callback; operators see no layout jump.

---

## Audit follow-up — Stages 1–16 (2026-05-14 → 2026-05-15)

Sixteen-stage cleanup pass executed against the consolidated audit
build. Phases 1–2 (Stages 1–11) closed every issue from the user's
`issues.txt` report. Phase 3 (Stages 12–15) closed gaps documented
in `AUDITARR-UNSURFACED-BACKEND.md` — capabilities the backend
shipped but never surfaced to operators. Stage 16 is consolidation.

### Phase 1 — User-reported fixes (Stages 1–7, Issues 1–11 + 25)

**Stage 1 — Button defaults (Issues 5, 25).** `Button` defaults to
`type="button"` so form-internal buttons no longer auto-submit.

**Stage 2 — App shell layout (Issues 1, 2, 3).** App shell scroll/
overflow fixes in `.app-main` and `.app-main-top`.

**Stage 3 — Files sort + codec filter (Issue 9).** Files page sort
contract pinned with backend-whitelisted sort keys and an
`Array.isArray` guard around the codec list. Three new sortable
columns (severity, video_codec, container), explicit
`severities_empty=true` sentinel so toggling every severity off
returns zero rows instead of falling through to "no filter",
optional matched-rules chip column, scope tri-state (all/media/
non-media).

**Stage 4 — Rule operator labels (Issues 6, 20).** Visual rule
builder uses human-readable operator labels; priority hint added.

**Stage 5 — Live app version (Issue 11).** Sidebar shows the live
`/system/version` value (was hardcoded `v1.0`).

**Stage 6 — Settings tabs (Issue 8).** Settings page broken into
Workspace / System / Integrations / Security tabs.

**Stage 7 — Dashboard scan controls (Issue 7).** Dashboard has
Run-scan controls + "Last scanned X ago" line.

### Phase 2 — User-reported fixes (Stages 8–11, Issues 10, 13, 15, 16)

**Stage 8 — Async scans + UX polish (Issue 10).** Scanner emits
`scan.progress` events every 100 files. New `POST /scans/all`
admin endpoint enqueues a scan for every enabled library.
`enqueue` default flipped to True so scans run async by default.
Enable/Disable rows show explicit "Active"/"Paused" pills + Pause
/Activate buttons (Automation, Optimization, Integrations).
Frontend adds `useScanProgress` WebSocket hook, `ScanProgressBar`,
`FilesRunScanButton` split-button.

**Stage 9 — Integration edit + rule action surface (Issues 11, 25).**
Integrations can now be edited via `IntegrationConnectDialog` in
edit mode (secrets blank with "Leave blank to keep existing").
Rule action vocabulary gains `quarantine(reason)`, `delete(confirm)`,
`delete_paths` (Stage 9 audit gain — gives operators a structured
quarantine flow instead of free-text rule definitions). New
`MediaExtensionRule` model + migration 0019 + admin CRUD at
`/api/v1/system/extension-rules`. Scanner honors four dispositions
(ignore / stats_only / malicious / accepted). VirusTotal deferred.
Disposition typed as `Literal[...]` to avoid a Pydantic
JSON-serialization bug when validation errors propagate.

**Stage 10 — Automation tab merge (Issue 15).** Automation merged
into Rules page as a tab. `/automation` redirects to
`/rules?tab=automation`. URL-driven tab state. Stage 10 was also
the long-uptime hardening pass: verified WebSocket disconnect in
`finally`, Redis `_reset_clients` + 5s cooldown reconnect lock,
plugin loader's `async with httpx.AsyncClient`, strong refs on all
`create_task` sites, single-flight refresh via `apiClient.refreshPromise`.

**Stage 11 — Dashboard collapsible sections + help discoverability
(Issue 16).** Eight dashboard sections collapsible via
chevron-in-`CardHead actions`; reset-layout button appears when
anything is collapsed; state persists via `useUiStore` →
`localStorage`. HelpDrawer width responsive
(`max-w-md md:max-w-xl lg:max-w-2xl`); loading state switched from
`isLoading` to `isPending && !data` for the correct first-paint
behaviour. New docs pages: `docs/dashboard/issues-threshold.md`,
`docs/optimization/profile-editor.md`, `docs/account/profile.md`,
`docs/settings/extension-rules.md`. `docs/rules/actions.md`
rewritten for the new action vocabulary.

### Phase 3 — Latent capabilities surfaced (Stages 12–15)

These four stages do not correspond to user reports — they close
gaps documented in `AUDITARR-UNSURFACED-BACKEND.md` where the
backend already had the capability but no UI affordance existed.

**Stage 12 — Playback insights surface.** Backs `playback_events`
with a usable read API + dashboard card. New
`PlaybackStatsService` exposes 6 endpoints (events list, top
transcoded, device matrix, decision trend, cursors list, reset
cursors). New `PlaybackStatsCard` with 3 tabs (Top transcoded,
Device matrix, Decision trend) on the dashboard. FileDetailDrawer
gains a Playback history section (hidden when empty).
IntegrationRow shows "Last polled X ago" for Plex/Jellyfin only,
admin-only "Reset cursor" link. New `playback` invalidation
namespace.

**Stage 13 — Tags everywhere.** The `media_tags` table was
populated by rule actions and Sonarr/Radarr/Bazarr tag sync but
not readable from any UI surface. New `MediaTagRead` schema; new
`MediaFilter.include_tags` + grouped query with case-sensitive
in-Python dedupe so "4K" and "4k" remain distinct per the audit's
guard rail. New `GET /media/{id}/tags` endpoint (404 unknown,
empty array known-but-tagless). New `MediaTag` interface + 
`useMediaTags` hook. FileDetailDrawer gains a Tags section
between Matched rules and Playback history, grouped by source
("Manual" / "From rules" / "From Sonarr" with fallback labels).
Optional `tags` column in `FILES_COLUMNS` (default OFF), 3 chips +
`+N` overflow. New `useSyncTags` hook + Sync tags button on
IntegrationRow (visible only for Sonarr/Radarr/Bazarr AND admin
users).

**Stage 14 — Operator tooling.** Five small admin surfaces wired
up. (A) Audit log viewer at `/settings/audit` with date-range
filters and cursor-style "Load more" — audit endpoint extended
with `since`/`until`/`before_id`, ORDER BY changed to `id.desc()`
for stable cursor under heavy insert load. (C) Manual housekeeping
trigger + last-run report — new `housekeeping_runs` table +
migration 0020; `HousekeepingService.run(trigger=...)` persists a
history row marked `manual` (admin endpoint) or `scheduled`
(cron). (D) Docs reload button. (E) Per-scan detail page at
`/scans/:scanId`; Dashboard's Recent scans rows are clickable +
keyboard-accessible; failed scans surface their `error` blob in a
`<pre>` block. (F) Per-item Run-now button on OptimizationQueueRow
verified pre-existing; pinned with 3 visibility tests. Sub-surface
B deferred to Stage 14b.

**Stage 14b — Per-rule Matched files tab (deferred from Stage 14).**
New `GET /rules/{rule_id}/matched-files` endpoint returns a
file-joined row per evaluation (`media_file_id`, `library_id`,
`path`, `filename`, `severity`, `severity_rank`, `evaluated_at`).
Inner-join filters out evaluation rows whose media file has been
evicted. New tab in `RuleEditorTabStrip` between Dry-run and JSON.
New `MatchedFilesTab` component renders the table with
click-through to `/files?file_id=<id>`. New deep-link handler in
`useFilesPageState` opens the drawer for the named file on mount
and strips the param via `history.replaceState` so back-nav
doesn't re-open.

**Stage 15 — Notification provider completeness.** Webhook
provider extended with custom HTTP method (POST/PUT enum), custom
headers (object schema, additionalProperties string→string), and
optional HMAC-SHA256 body signing via `webhook_secret` secret +
`secret_header_name` config — both must be set or signature is
omitted (defense in depth). Body bytes serialized once via
`json.dumps(payload, separators=(",", ":"))` so the signature is
verifiable byte-for-byte against the wire body. Frontend
`NotificationDynamicInput` extended with an `object` variant
rendering a key/value editor for the new headers field; pending
empty-key rows tracked in local state so Add Header doesn't
depend on the parent committing back an empty pair.

### Phase 4 — Consolidation (Stage 16)

**Stage 16 — Cumulative test sweep, top-level changelog,
completion report, smoke script.** New `scripts/post-fix-smoke.sh`
hits the seven endpoints touched by the audit phases and asserts
each returns HTTP 200 (and that `email` + `webhook` both appear in
`/notifications/kinds`). Appendix B at the foot of this file
summarises issues closed, latent bugs found and fixed, latent
capabilities surfaced, anything deferred (with rationale), and the
before/after test counts.

### Test counts after Stage 16

| Suite | Count | Notes |
|---:|---:|---|
| Backend unit | 328 | Up from 322 at start of audit cycle. |
| Backend integration | 495 | Across 56 files. `test_cli_user_commands.py` excluded — pre-existing alembic-vs-SQLite issue, see Stage 1 changelog. |
| Frontend | 316 | Across 44 files. |
| **Total** | **1,139** | All green. |

### Deferred items (see Appendix B)

- **VirusTotal hook (Stage 9b candidate).** Plugin scaffold ships;
  the actual hook surfacing is deferred per the audit plan.
- **Extension rules settings panel UI.** Stage 9 shipped the model
  + CRUD endpoints + scanner integration; a dedicated settings
  panel was not built.
- **Actor-id autocomplete on the audit page.** Operators type the
  UUID by hand for now.
- **Live toast on optimization Run now.** The button works
  end-to-end via React-Query invalidation; toast surfacing is
  deferred polish.
- **Bulk-sync tags across integrations.** Per-integration only
  today.

---

## Bug-hunt 3 — 2026-05-12

Permissions & input-validation audit. Forensic walk through every
v1 router file looking for mutation endpoints that use
`CurrentUser` instead of `AdminUser`, unauthenticated endpoints
that leak operationally-sensitive data, and input-validation
gaps in Stage 32's freshly-shipped upload path. **Three real
bugs found and fixed.** No version bump — these are security
patches that don't change documented contracts.

### Fixed — `POST /integrations/{id}/healthcheck` was open to any authenticated user

Triggering a healthcheck makes an outbound HTTP request against
the admin-configured integration `base_url` and surfaces network
detail in the response — both operationally sensitive:

- A non-admin user could repeatedly trigger healthchecks against
  internal-network targets (every integration is admin-configured,
  but the *act* of forcing the request still matters for
  DDoS-style abuse of an admin-configured upstream).
- The response body returns `detail` which may include error
  messages from the upstream, leaking network topology details.

The rest of the integration write surface (create/update/delete/
test/sync) is already admin-only; this endpoint was an
inconsistency.

Fix: `_user: CurrentUser` → `_admin: AdminUser`. Existing test
already uses admin headers, so no contract regression.

### Fixed — `GET /system/info` was unauthenticated

Leaked `platform.platform()` (host OS + kernel version) and
`sys.version` to anonymous callers. Low-severity reconnaissance
info that helps an attacker scope known CVEs against the running
host.

Fix: requires `CurrentUser` (any authenticated user, including
viewers). `/system/version` stays open because it's the polling-
friendly probe designed for the login-screen sidebar and only
returns the app version — no host detail.

### Fixed — Plugin upload zip bomb protection (Stage 32 follow-up)

The Stage 32 install endpoint capped *compressed* upload size at
16 MiB but had no limit on uncompressed extraction. A
high-compression-ratio zip (`42.zip`-style nested archive, or
a 1 MiB zip of zeros that decompresses to many GiB) could fill
the operator's disk.

Fix: in `_extract_zip_to_plugin_dir`:

1. **Pre-extraction check** — sum claimed uncompressed sizes
   from the zip's central directory and bail with a 422 if the
   total exceeds 128 MiB. The check happens before any disk
   write, so a hostile upload never leaves bytes on disk.

2. **Streamed check (defense in depth)** — replace
   `shutil.copyfileobj` with an explicit 64 KiB-chunk loop that
   tracks bytes written and aborts mid-stream if the running
   total exceeds the cap. Protects against archives that lie
   about their uncompressed sizes in the central directory.

The 128 MiB cap is 8× the upload size limit — well past any
reasonable plugin (largest first-party plugins are well under
1 MiB) while catching obvious attack payloads.

### Surveyed and found clean

Endpoints I checked and decided to leave alone, with notes on
why each is intentional:

- **`POST /rules/dry-run`** — open to `CurrentUser` is fine.
  Read-only evaluation; the metadata it could leak is already
  exposed via `GET /media/{id}` to the same role tier.

- **`/docs/*`** — public by design (header comment confirms).
  The `:path` parameter doesn't enable filesystem traversal
  because the documentation service is an in-memory dict
  lookup, not a filesystem read.

- **Runtime settings, audit log, system config** — all
  admin-only. Verified.

- **Bulk endpoints (media + optimization)** — all admin-only
  with 500-item caps and duplicate-id rejection. Verified.

- **Auth endpoints** — rate-limited (Stage 2). Password reset
  returns generic "accepted" regardless of email existence
  (non-enumerable). Verified.

- **WebSocket** — JWT auth required (Stage 14). No per-topic
  permission filtering, but a viewer can't see WS events they
  couldn't already see via HTTP. By design.

- **Self-service endpoints** (`POST /logout-all`, `PATCH /me`,
  `POST /password/change`) — correctly use `CurrentUser`
  because users are operating on their own account.

### Tests — 8 new

`test_bughunt3_permissions.py`:

- **Healthcheck (3 tests):** viewer gets 403; admin still gets
  200; the stub provider is registered in the fixture so the
  full path exercises.

- **System info (4 tests):** anon gets 401; viewer gets 200
  with full body; admin gets 200; `/system/version` stays open
  to anon AND its response does NOT contain `platform` or
  `python` (the sensitive fields stay on `/info`).

- **Zip bomb (2 tests):** normal-sized plugin zip still
  installs (sanity); a zip with a 200 MiB claimed-uncompressed
  member is rejected at 422 with "expand" or "bomb" in the
  message AND nothing is written to disk.

### Honest scope notes

- **No streamed-abort test for the lying-zip path.** Crafting
  a zip with valid CRC for a payload that misreports its size
  in the central directory is its own engineering project —
  Python's `zipfile` validates CRC-32 on close. The streamed
  check is defense-in-depth behind the central-directory check
  which IS tested. The streaming code lives at the right layer;
  testing it would require building a custom zip writer that
  emits valid CRCs for fictitious size declarations. Documented
  in the test file as a known coverage gap.

- **No fix for the WebSocket per-topic permission idea.**
  Mentioned during survey but rejected: viewer-readable events
  on the bus all correspond to data viewers can already fetch
  via HTTP. Adding topic-level ACLs would be feature-work, not
  bug-fixing.

- **No new ledger items.** Each finding either landed as a fix
  (3 of them) or surveyed-and-clean (everything else).

### Test counts

- Backend: **674/674 pass** (+8 from 666)
- Frontend: **144/144 pass** (unchanged — frontend untouched)
- Combined: **818/818**

### Deferred-stages ledger (unchanged)

- ✅ Stage 23 (closed at Stage 28)
- ✅ Stage 24 (closed at Stage 30)
- **Stage 25**: ~~upload~~ ✅, ~~uninstall~~ ✅; gallery
  install deferred indefinitely, soft enable/disable deferred
  to Stage 33.
- **Stage 26**: ~~codec/container filter~~ ✅; daily severity
  snapshot store remains.
- **Bug-hunt 1**: 3 cosmetic items remain.
- **Bug-hunt 2**: 2 minor optimizations remain.

## [1.17.0] — 2026-05-12

Stage 32: **plugin lifecycle endpoints — upload (install) and
uninstall**. Closes two of the four Stage 25 ledger items. Soft
enable/disable and gallery install remain deferred (each needs a
new persistence model + loader-discovery changes, scoped for a
future stage).

Before Stage 32, plugins were SCP-into-volume-and-restart only.
The operator UI surface was list + reload + configure. Now an
admin can drop a zip into the Plugins page to install live; a
matching uninstall affordance lives on each installed row, with
a destructive-action confirmation modal.

### Added — backend

- **`PluginLoader.install_from_zip(zip_bytes, *, app, route_prefix)`**
  (~200 new lines in `loader.py`):
  - Parses the manifest from inside the zip in memory **before**
    any disk write. A bad upload never leaves a half-extracted
    directory behind.
  - Schema-validates via `PluginManifest.model_validate` — 422
    on bad manifest with the validation error surfaced verbatim.
  - Refuses id collisions (409). Operator's path to "I want to
    update this plugin" is `uninstall` then `install`, or
    `reload` to swap files in place when the id stays the same.
  - Uses the per-plugin lock from Bug-hunt 2 to serialize
    concurrent uploads of the same id.
  - Extracts to `settings.plugin_dir` (the operator-managed
    directory) — **explicitly NOT** `settings.plugin_directories[0]`
    which is `builtin_plugin_dir` (shipped reference plugins).
    See "Test isolation bug found and fixed" below.
  - Renames the zip's wrapper directory to match `plugin_id` so
    operators don't need a specific archive layout.
  - Zip slip protection: any member path containing `..` or
    starting with `/` is rejected before any extraction.
  - Multi-top-level-dir zips are rejected with a clear message.
  - Rollback: if extraction succeeds but `_load_one` fails, the
    partial install is `rmtree`-cleaned so the operator isn't
    left with a half-installed plugin.

- **`PluginLoader.uninstall(plugin_id)`** (~80 new lines):
  - Acquires the per-plugin lock (idempotent under concurrent
    uninstall attempts).
  - Runs `on_shutdown` + `on_unload` for live instances.
  - Drops the module from `sys.modules` so a re-install picks
    up fresh code rather than a cached import.
  - **Removes loader state BEFORE touching disk** so a partial
    deletion can't leave a "loaded" record pointing at half-
    deleted files.
  - Deletes the directory with `ignore_errors=True` (Windows
    file-lock tolerance) and warns the operator if anything
    survived; the loader state is still clean either way.
  - **Plugin settings rows persist across uninstall** — re-
    installing the same plugin id picks them up automatically.
    Operators almost always want their config back. A future
    `DELETE /plugins/{id}/settings` endpoint will handle the
    "clear config too" case if it's needed.
  - Warns the operator about FastAPI's route-removal
    limitation: routes mounted by a routed plugin can't be
    unregistered at runtime and will return import errors
    until a process restart.
  - 404 on unknown plugin id; idempotent (second call after
    successful uninstall also returns 404, not an internal
    error).

- **`POST /api/v1/plugins/install`** — `UploadFile` form field,
  16 MiB cap (well above any reasonable plugin, well below OOM
  risk on a misbehaving client), admin-only.

- **`DELETE /api/v1/plugins/{plugin_id}`** — admin-only.
  Returns `{id, removed: true, warnings: [...]}`.

### Added — frontend

- **`useInstallPlugin()`** — `useMutation` wrapping
  `apiClient.postForm("/plugins/install", formData)`.
  Invalidates the plugins-list query on success.

- **`useUninstallPlugin()`** — `useMutation` wrapping
  `apiClient.delete<UninstallResult>("/plugins/{id}")`.
  `UninstallResult` exported so the page can iterate over
  `warnings` for the success toast.

- **`PluginsPage` toolbar** — "Install plugin" primary CTA on
  the right side of the toolbar. Hidden `<input type="file"
  accept=".zip">` triggered programmatically; `e.target.value
  = ""` after each pick so re-selecting the same file fires a
  fresh change event. Button label flips to "Installing…" with
  a spinning refresh icon during the upload.

- **`InstalledTable`** rows — new "Uninstall" affordance opens
  the confirmation modal (does NOT fire the mutation directly).
  Reload + Configure affordances unchanged from Stage 25.

- **`UninstallConfirmDialog`** — destructive-action confirmation:
  - `role="dialog"`, `aria-modal="true"`, `aria-labelledby`
    pointing at the title (a11y).
  - Plain-language explanation that files will be deleted and
    settings will persist.
  - Conditional warning paragraph when `plugin.routes` is true,
    surfacing the FastAPI route-unmount limitation.
  - Cancel button is `autoFocus` so an accidental Enter
    press doesn't trigger uninstall.
  - Confirm uses the `danger` variant with a trash icon.
  - Click-outside on the backdrop closes; Escape closes; both
    blocked while the mutation is pending.

### Test isolation bug found and fixed

When I first ran the Stage 32 backend tests, the dev
`backend/plugins/` directory got contaminated with test plugins
because:

1. The loader's `plugin_directories` property returns BOTH
   `builtin_plugin_dir` (shipped plugins) AND `plugin_dir` (user
   plugins).
2. My first draft of `install_from_zip` wrote to
   `directories[0]`, which is `builtin_plugin_dir`.
3. The test fixture only set `AUDITARR_PLUGIN_DIR`, not
   `AUDITARR_BUILTIN_PLUGIN_DIR`, so the loader was happily
   writing to the dev plugin directory.

I restored `backend/plugins/` from the bughunt2 snapshot (7
reference plugins: bazarr, example-hello, jellyfin, plex,
radarr, sonarr, tdarr) and made two correctness changes:

1. **Loader code:** `install_from_zip` now explicitly writes to
   `self._settings.plugin_dir` (the operator-managed dir) with
   an inline comment explaining why — operator uploads should
   never land in the same directory as Auditarr's shipped
   reference plugins; mixing them makes "which plugins did I
   install vs which ship with the product" impossible to
   answer from disk.
2. **Test fixture:** sets both `AUDITARR_PLUGIN_DIR` and
   `AUDITARR_BUILTIN_PLUGIN_DIR` to tmp paths, fully isolating
   the test from the dev plugin set.

The loader fix survived because it's the right architecture
regardless of whether the two dirs are the same path or
different.

### Honest scope notes

- **Soft enable/disable deferred to Stage 33.** Would need a
  new `PluginState` model + migration + loader-discovery
  changes (skip-loading rows where `enabled=False`). I drafted
  the model and migration mid-stage, then reverted them when I
  realized the scope was creeping past one slot. The right
  shape for Stage 32 is install + uninstall only.

- **Plugin install from gallery indefinitely deferred.** Needs
  a real plugin registry, which is its own product decision.
  The `install_source="upload"` field in the `plugin.installed`
  event bus emit is forward-compatible — a future gallery
  installer would use `install_source="gallery"`.

- **Routes can't be unmounted at runtime.** FastAPI limitation.
  Surfaced as a warning in the uninstall response and in the
  confirmation dialog for routed plugins. Operators get the
  honest answer — there's no clever workaround.

- **No new CSS this stage.** The `UninstallConfirmDialog` uses
  the `.dialog-*` family from Stage 22; the toolbar uses the
  `.rules-toolbar` family from Stage 24. CSS bundle stayed at
  18.52 KB. The reusable-primitive economy paid back again.

### Tests — 25 new (16 backend + 9 frontend)

**Backend (`test_plugin_stage32.py`):**

- install — happy path: extracts files, loads, summary in list.
- install — id collision returns 409 with a clear message.
- install — bad zip returns 422 with "zip" in the message.
- install — missing manifest returns 422 with "manifest" in
  the message.
- install — invalid manifest schema (bad id format) returns 422.
- install — zip slip path rejected: 422 + no extraction on disk.
- install — multiple top-level dirs rejected with 422.
- install — oversized upload (>16 MiB) rejected with 422.
- install — non-admin user gets 403.
- install — no auth gets 401.
- uninstall — happy path: removed from loader, files gone.
- uninstall — second call returns 404 (idempotent).
- uninstall — unknown plugin returns 404.
- **uninstall — settings persist across uninstall + re-install.**
  Direct DB inspection: write `PluginSettings` row, uninstall,
  verify row still exists with original values.
- uninstall — non-admin gets 403.
- end-to-end round-trip: install → list → uninstall → list →
  install again with same id (no leftover state blocking).

**Frontend (`PluginsPage.stage32.test.tsx`):**

- Install button visible on the toolbar.
- Clicking it triggers the hidden file input.
- Picking a file POSTs (multipart) to `/plugins/install`.
- FormData carries the picked File under the `file` field.
- Install success fires an OK toast with the plugin name.
- Install 409 surfaces the server's message verbatim in an
  error toast.
- Per-row "Uninstall" opens the confirmation modal (does NOT
  fire DELETE directly).
- Confirming the modal DELETEs `/plugins/{id}`.
- Cancelling the modal closes it without DELETEing.

### Test counts

- Backend: **666/666 pass** (+16 from 650)
- Frontend: **144/144 pass** (+9 from 135)
- Combined: **810/810**

### Deferred-stages ledger update

- **Stage 23**: ✅ ✅ ✅ (closed at Stage 28)
- **Stage 24**: ✅ ✅ (closed at Stage 30)
- **Stage 25**: ~~plugin upload from UI~~ (closed at Stage 32);
  ~~plugin uninstall mechanism~~ (closed at Stage 32); plugin
  install from gallery; soft enable/disable plugin state.
- **Stage 26**: ~~codec / container filter on Files~~ (closed
  at Stage 31); daily severity snapshot store.
- **Bug-hunt 1**: dedupe `Field` / `Input` into shared
  primitives; migrate four ad-hoc modal wrappers to Stage 22
  `.dialog-*` vocabulary; add Integrations dialog regression
  test.
- **Bug-hunt 2**: runtime-settings publish dedupe; React Query
  dashboard invalidation on media mutations.

## [1.16.0] — 2026-05-12

Stage 31: **codec / container filter on Files** — the higher-
priority of the two remaining Stage 26 ledger items. The
dashboard's `/categories` panel already groups files by
`video_codec` and `container` with file counts; this stage adds
the obvious next move: clicking through to a filtered Files
view ("I see a codec spike on the dashboard → show me those
files"). The dashboard cards can now deep-link via
`?video_codec=hevc&container=mp4`.

### Added — backend

- **`MediaFilter`** extended with `video_codec: str | None` and
  `container: str | None`. Both accept comma-separated values
  that become IN clauses; single values become equality. Empty
  CSV is silently dropped (the UI may send `hevc,` while the
  operator is deselecting; the server doesn't treat the empty
  fragment as a literal match-the-empty-string filter).

- **`MediaRepository.list`** applies the new filters with the
  same shape as the existing severity filter — splits on
  commas, strips empties, picks `==` for length-1 and `.in_()`
  for length-N. SQL `IS NULL` semantics correctly exclude
  unprobed rows (where `video_codec` is `NULL`) when a codec
  filter is active.

- **`GET /api/v1/media`** exposes `video_codec` and `container`
  query params with `max_length=512` and composes them with all
  existing filters.

### Added — frontend

- **`MediaFilters`** interface extended with `video_codec?:
  string` and `container?: string`. The existing `useMediaList`
  URLSearchParams loop forwards any non-empty field, so no hook
  changes were needed.

- **`CodecFilterMenu.tsx`** (~210 lines) — popover-based
  two-section picker with checkboxes for codecs (top) and
  containers (bottom), each row showing the file count from the
  dashboard `/categories` endpoint. Pulls options from
  `useDashboardCategories(64)` so operators only see codec /
  container values that actually appear in their library — no
  long-tail-of-ffprobe codecs to scroll past.

  Trigger button shows total active count as a mono badge
  (`activeCodecs.size + activeContainers.size`); click-outside
  + Escape close behavior; "Clear all" + "Done" footer actions.
  Uses the existing `.popover` family — zero new CSS.

- **`FilesPage`**:
  - Two new `useState<Set<string>>` for `activeCodecs` and
    `activeContainers`.
  - URL deep-link from `?video_codec=hevc&container=matroska`
    (extends the existing `?severity=` / `?library_id=`
    pattern).
  - `filters` memo sort-and-joins the sets to keep React Query
    cache keys stable across renders.
  - Selection-reset effect dep array includes the new filters.
  - `FilterToolbar` signature extended with five new props
    (`activeCodecs`, `activeContainers`, `onToggleCodec`,
    `onToggleContainer`, `onClearCodecsAndContainers`), renders
    `<CodecFilterMenu>` between the quarantine select and the
    column-visibility menu.

### Discovered + scope notes

The Stage 31 workspace had **half-finished scaffolding from a
prior partial session** — `FilesPage.tsx` referenced
`activeCodecs` / `activeContainers` state that wasn't declared
(breaking typecheck), and `CodecFilterMenu.tsx` existed as a
fully-formed component using the popover primitives. The
recovery move:

  - Restored `FilesPage.tsx` from the Bug-hunt 2 snapshot
    (md5 confirmed they differed) and re-built the state
    additions cleanly.
  - Audited `CodecFilterMenu.tsx` carefully before keeping it
    — the component follows the same patterns as Stage 23's
    `ColumnVisibilityMenu` (same `.popover` family, same
    click-outside/Esc handling, same trigger-with-count shape).
    Rewriting would have been ~200 lines of identical code.
    Kept it after removing one unused `cn` import.

Honest scope notes:

- **Codec / container values aren't lowercased on input**
  unlike `extension` — ffprobe emits stable lowercase values
  and the UI sources options from the dashboard endpoint
  (already canonical).

- **Picker uses `/dashboard/categories?limit=64`** rather than
  a dedicated `/media/codec-vocabulary` endpoint. The dashboard
  endpoint already returns the right shape; adding a parallel
  endpoint would be over-engineering. `limit=64` is generous
  — real libraries show 3-6 codecs + 3-6 containers — and the
  menu paints all returned items without internal scrolling.

- **Clear-all behavior + React Query caching:** clicking
  "Clear all" returns the filter set to its initial empty
  state, which is the same cache key as the mount-time fetch.
  React Query serves the cached result rather than re-fetching
  (within its 10s `staleTime`). This is correct behavior. The
  user-visible signal that the filters cleared is the trigger
  button's badge count going from `2` (or whatever) back to
  the bare "Codec / container" label. The Stage 31 test
  asserts that signal directly — not "next API call drops the
  params", which would be a wrong model of React Query
  behavior.

- **No new CSS this stage.** The `.popover` family from Stage
  23, `.settings-input` from Stage 22, and the popover footer
  buttons all carry the visual load.

### Tests — 18 new (9 backend + 9 frontend)

**Backend (`test_media_stage31.py`):**

- Single `video_codec` filter narrows results to exactly the
  matching rows.
- Multi-value `video_codec` (comma-separated) becomes an IN
  clause (3 hevc + 2 h264 = 5).
- Single `container` filter works the same way.
- Multi-value `container` filter (mp4,avi = 3 rows).
- Both filters compose with AND, not OR — `video_codec=h264
  &container=mp4` returns 2; `video_codec=h264&container=
  matroska` returns 0 (no h264-in-matroska rows exist).
- Unprobed rows (`video_codec IS NULL`) correctly excluded
  when a codec filter is active.
- Empty CSV (`?video_codec=,,,` or `?video_codec=hevc,`)
  silently dropped — trailing-comma deselection state from
  the UI doesn't trigger a match-empty-string filter.
- Composes with existing severity filter (`video_codec=hevc
  &severity=warn` → only matching rows).
- Pagination stable under filter (page 1 + page 2 yield 3
  distinct rows, no duplicates, no skips).

**Frontend (`FilesPage.stage31.test.tsx`):**

- Trigger button rendered in the toolbar.
- Popover lists codec and container options with file counts
  on click.
- Selecting a codec sends `?video_codec=<key>` to `/media`.
- Selecting two codecs produces a sorted comma-joined value
  (`h264,hevc` not `hevc,h264` — sorted alphabetically to keep
  React Query cache keys stable).
- Selecting a container sends `?container=<key>`.
- Active-count badge appears on the trigger button when
  filters are applied.
- Escape closes the popover.
- "Clear all" empties both sets (asserted via the trigger
  badge reverting to the bare label).
- Deep-link from `?video_codec=hevc&container=mp4` in the URL
  initializes both filters on mount.

### Test counts

- Backend: **650/650 pass** (+9 from 641)
- Frontend: **135/135 pass** (+9 from 126)
- Combined: **785/785**

### Deferred-stages ledger update

- **Stage 23**: ✅ ✅ ✅ (closed at Stage 28)
- **Stage 24**: ✅ ✅ (closed at Stage 30)
- **Stage 25**: plugin upload from UI; plugin uninstall
  mechanism; plugin install from gallery; soft enable/disable
  plugin state.
- **Stage 26**: ~~codec / container filter on Files~~ (closed
  at Stage 31); daily severity snapshot store.
- **Bug-hunt 1**: dedupe `Field` / `Input` into shared
  primitives; migrate four ad-hoc modal wrappers to Stage 22
  `.dialog-*` vocabulary; add Integrations dialog regression
  test.
- **Bug-hunt 2**: runtime-settings publish dedupe; React Query
  dashboard invalidation on media mutations.

## Bug-hunt 2 — 2026-05-12

Concurrency and idempotency audit. Forensic walk through the
surfaces where Auditarr handles parallel operations, retries,
and state transitions. **Four real bugs found and fixed.** No
version bump — these are bug fixes that don't change API
contracts.

### Fixed — optimization worker SELECT-then-UPDATE race

`OptimizationWorker._claim_next` was a naive SELECT followed by
ORM-level UPDATE. The original code's comment acknowledged it
("Stage 13 will add row locks…"). Stage 13 never landed that
work.

The race: two concurrent `run-next` clicks (operator double-
click; manual click + `optimization_tick` cron) could both
SELECT the same queued item and both call `_mark_running`.
Consequences:

- Duplicate `optimization.started` events fired.
- The same ffmpeg job ran **twice** on the same input file.
- `started_at` clobbered mid-run; `progress_pct` reset.
- Potential destructive-rename race at the swap step.

Fix: single-statement conditional `UPDATE ... WHERE id=:id AND
status='queued'` with `rowcount` check. If `rowcount == 0` we
lost the race; retry up to 16 times before reporting idle.
Portable across SQLite/Postgres/MySQL — no `SKIP LOCKED` or
`BEGIN IMMEDIATE` ceremony. Uses `synchronize_session="fetch"`
so the SQLAlchemy identity-map cache is invalidated for the
affected row.

### Fixed — concurrent scans of the same library

`POST /scans/libraries/{library_id}` had no check for an
in-progress scan on the same library. Two POSTs (operator
double-click; automation tick + manual click) each kicked off a
scanner against the same directory:

- Duplicate `scan.started` events fired.
- ffprobe ran on every file twice — CPU/IO waste at library
  scale.
- Two `ScanRun` rows showed as "running" in the UI, confusing
  operators about which one to watch.
- The new files were upserted-by-path twice but the second
  scan's run completion clobbered the first's stats.

Fix: new `ScanRepository.find_active_for_library()` queries for
queued-or-running scan runs; `trigger_scan` rejects with
`ConflictError` (HTTP 409) if one exists. Per-library
single-flight — different libraries still scan concurrently.

### Fixed — `cancel_item` and `retry_item` SELECT-then-UPDATE races

Same pattern as the worker bug, lower-impact. Two concurrent
cancel clicks on a queued item would both pass the pre-check,
both write `status=cancelled`, both commit, both emit
`optimization.failed`. The duplicate event lights up
notifications twice and confuses dashboards. The same shape
applied to retry: two clicks would both write `queued_at = now`,
clobbering FIFO order.

Fix: atomic conditional UPDATE with `rowcount` short-circuit.
The losing caller observes `rowcount == 0`, reloads the row,
and returns the current state idempotently — no second event
emission, no second `queued_at` overwrite.

### Fixed — plugin `reload_one` had no per-plugin lock

Two concurrent `POST /plugins/{id}/reload` calls against the
same plugin id would race: both run `on_shutdown`/`on_unload`,
both drop the module from `sys.modules`, both reimport, both
call `on_startup`. Background tasks the first `on_startup`
registered survived while the second `on_startup` ran on a
fresh-but-conflicting copy of the module — undefined event-bus
subscription accounting, dangling tasks, half-initialized
`instance` exposed via `self._plugins`.

Fix: lazily-created `asyncio.Lock` per plugin id in
`self._reload_locks`. Reload of the same plugin serializes;
reloads of different plugins still run concurrently (the lock
is per-plugin, not global).

### Surveyed and found clean

- **`upsert_queued` repository method**: correctly idempotent on
  the `(media_file_id, profile)` key; only mutates rows that are
  still `queued`. Stage 7 designed this well.
- **Runtime-settings publish/subscribe ordering**: the publish
  happens AFTER the commit, so the subscriber's
  `load_and_apply_overrides` always reads a freshly-committed
  row. No race window. Stage 21 designed this well.
- **Reload listener serial processing**: `async for message in
  pubsub.listen()` processes one message at a time. Rapid
  setting changes generate redundant publishes (wasteful but not
  incorrect).
- **React Query bulk-mutation invalidations**: target the right
  keys (`["media"]`, `["optimization"]`). Re-evaluation +
  reprobe + quarantine + bulk-enqueue all correctly invalidate
  the list query.
- **Bulk endpoints' duplicate-id rejection**: consistent 422
  across the codebase — `media_ids` is deduped at the request
  schema level.

### Tests — 10 new (all backend)

`test_bughunt2_concurrency.py`:

- **Bug 1 — atomic worker claim:** seed one queued item; call
  `_claim_next()` twice. First call claims and marks running;
  second call returns `None` (no other queued items). Pre-fix
  this would race; the conditional UPDATE prevents the
  double-claim.

- **Bug 2 — concurrent scan rejection:** seed a running
  `ScanRun`; POST `/scans/libraries/{id}`. Server returns 409
  with "already" in the message.

- **Bug 2 — completed scan doesn't block:** seed only completed
  scans; new POST proceeds (not 409). The single-flight check
  filters by `status.in_(['queued', 'running'])`.

- **Bug 2 — `find_active_for_library` repo unit tests:** two
  tests covering the returns-`None` when only completed/failed
  rows exist, and the returns-the-row when a queued row exists.

- **Bug 3 — cancel idempotent on already-cancelled:** first
  cancel succeeds with 200; second cancel returns 422 cleanly
  (the pre-check catches it).

- **Bug 3 — cancel rejects non-cancellable state:** completed
  item can't be cancelled; same 422 response surface.

- **Bug 3 — retry idempotent on already-queued:** first retry
  transitions failed → queued (200); second retry on the now-
  queued item returns 200 with the same queued state (no
  `queued_at` clobber).

- **Bug 4 — reload lock serializes same-plugin:** subclass
  `PluginLoader` to record the start/end of each
  `_reload_one_locked` call; fire two `asyncio.gather`d
  reloads of the same plugin id; assert the brackets don't
  overlap.

- **Bug 4 — different plugins still concurrent:** the same
  measurement pattern with two different plugin ids; assert
  the brackets overlap (total wallclock < 90ms vs the ~100ms
  serialized case).

### Honest scope notes

- **Testing concurrency in pytest is awkward.** `asyncio.gather`
  inside one event loop doesn't actually interleave at
  arbitrary points — coroutines yield only at await points. The
  tests use deterministic patterns that surface the bug class
  without needing real OS threads. The atomic UPDATE tests
  drive the codepath twice in sequence and assert that the
  second call observes the first's side effect via the
  conditional WHERE. The plugin-reload tests use real wall
  clock with a 50ms sleep inside the lock to make the
  serialization visible.

- **No DB-level row locking added.** A `SELECT FOR UPDATE SKIP
  LOCKED` (Postgres) or `BEGIN IMMEDIATE` (SQLite) would also
  solve these races and might be needed at higher concurrency
  scales. For today's workload — single FastAPI process, ARQ
  worker pool, low-volume queue — the conditional-UPDATE pattern
  is portable, sufficient, and doesn't bind us to a specific
  dialect.

- **No `synchronize_session="evaluate"`** used. SQLAlchemy
  recommends `evaluate` for performance but it's incompatible
  with `IN(...)` clauses on some dialects and stricter Python-
  side evaluation. `"fetch"` is one extra round-trip per UPDATE
  in exchange for never-being-wrong-about-the-identity-map.
  The trade is right for these endpoints (rare-ish writes).

- **Bug 5+ findings deferred:** runtime-settings publish
  redundancy (wasteful but correct); React Query dashboard
  invalidation on media mutations (would tighten the
  metrics-after-rules-change UX). Neither is a stability bug;
  both belong in a future polish stage.

### Test counts

- Backend: **641/641 pass** (+10 from 631)
- Frontend: unchanged (no frontend modifications)
- Combined: **767/767**

### Deferred-stages ledger update

- **Stage 23**: ✅ ✅ ✅ (closed at Stage 28)
- **Stage 24**: ✅ ✅ (closed at Stage 30)
- **Stage 25**: plugin upload from UI; plugin uninstall
  mechanism; plugin install from gallery; soft enable/disable
  plugin state.
- **Stage 26**: codec / container filter on Files; daily
  severity snapshot store.
- **Bug-hunt 1**: dedupe `Field` / `Input` into shared
  primitives; migrate four ad-hoc modal wrappers to Stage 22
  `.dialog-*` vocabulary; add Integrations dialog regression
  test.
- **Bug-hunt 2**: runtime-settings publish dedupe; React Query
  dashboard invalidation on media mutations.

## [1.15.0] — 2026-05-12

Stage 30: Closes the **last** Stage 24 ledger item — **routed
full-screen rule editor**. The modal `RuleDialog` is retired; rule
create + edit now lives at `/rules/new` and
`/rules/:ruleId/edit`. Built-in rules navigate to the editor in
read-only mode (banner + disabled inputs + Duplicate primary
CTA + Save hidden), so inspection-without-modify is a real
workflow now instead of a no-op row click.

The Stage 23 ledger has been empty since Stage 28; the Stage 24
ledger is now also fully closed.

### Added — frontend

- **`RuleEditorPage.tsx`** (~510 lines) — full-screen routed
  editor. Carries the form state, the Visual / Dry-run / JSON
  tab strip, and the dry-run panel that lived in the old
  dialog. Three structural improvements over the modal:
  - **Real URL** — operators can bookmark a half-finished edit
    or share it for review.
  - **Vertical room** — complex rules with deeply nested
    `all`/`any` matches no longer collide with the modal's
    fixed height.
  - **JSON-tab textarea grew to 24 rows** from the modal's 16
    (the page has the height to spare).

- **Read-only mode** for built-in rules:
  - Info banner at the top explaining the rule is codebase-
    owned and offering an inline "duplicate it" link.
  - Name / description / priority / enabled-toggle / Save
    button: hidden or disabled.
  - Visual tab swaps the interactive builder for a short
    "switch to JSON to inspect" message. Adding a `readOnly`
    prop to `VisualRuleBuilder` would have rippled through 5
    inner components; pointing to JSON was the right scope
    call.
  - Page-header primary CTA is **Duplicate as custom rule**
    — clicking POSTs `/rules/{id}/duplicate` and navigates
    back to the rules list.

- **Back button** in the page header (also Escape) returns to
  `/rules` without saving. Same mental model the modal had:
  "I'm editing → now I'm done."

- **404 / not-found** state — when the rule id doesn't resolve
  (deleted from another tab, bad URL, etc.) the page renders an
  EmptyState with a "Back to rules" link instead of crashing or
  showing a confusing loading-forever state.

- **`useRule(id)`** hook — single-rule fetch with React Query.
  `enabled: !!id` so the create-mode (`id` undefined) skips the
  query entirely.

### Changed — frontend

- **`AppRoutes.tsx`** registers `rules/new` and
  `rules/:ruleId/edit` routes.

- **`RulesPage.tsx`**:
  - Removed `editing` state and the `RuleDialog` render block.
  - "New rule" button → `navigate("/rules/new")`.
  - Custom-row click → `navigate(\`/rules/${r.id}/edit\`)`.
  - **Built-in row click → navigate too** (was: no-op in Stage
    29). The Stage 29 "cursor: default" treatment is removed
    since the row is now clickable.

- **`RuleRow`** in RulesPage no longer branches `onClick` on
  `is_builtin`. Both paths navigate; the editor renders the
  read-only mode for builtins.

- **`BuiltinTab`** signature gained an `onEdit` callback (was
  `() => undefined` in Stage 29). The Stage 29 docstring saying
  "builtin rows aren't clickable" was updated to reflect the new
  behavior.

### Removed — frontend

- **`RuleDialog.tsx`** (456 lines deleted). The migration
  directive calls for cleanup of replaced systems; the dialog
  has no remaining callers and a future modal-edit need (none
  on the ledger) would be a small wrapper around the page-level
  form rather than a verbatim revival.

### Quality improvement found during implementation

The old dialog's Save button used a DOM hack to find the form:
`closest('.dialog')?.querySelector('form')`. That worked in the
dialog because the button and form shared an ancestor with the
right class. Moving the button into the PageHeader actions broke
the ancestor relationship (the page header renders in its own
`<header>` element). Fixed properly with a `useRef<HTMLFormElement>`
attached to the form. The new pattern is structurally cleaner —
no DOM-walking, no class-name coupling — and would have been the
right shape in the dialog too. Inherited debt finally paid off.

### Honest scope notes

- **No side-by-side dry-run + visual builder.** The page-level
  real estate makes this feasible (vs. the modal's constraint),
  but it's its own slice of work. The tabbed layout is preserved
  for now.

- **The Visual builder isn't refactored for read-only.** The
  read-only path shows a "switch to JSON tab to inspect" panel
  instead. Adding a `readOnly` prop would have rippled through
  `ConditionEditor`, `ValueInput`, `ActionEditor`,
  `ActionArgInput`, and the value-input variants. The pragmatic
  "point to JSON" alternative is fine for today; the JSON tab IS
  read-only-friendly (the textarea gets `readOnly` directly).

- **Suggestion deployment flow uses its own modal**
  (`SuggestionReviewModal`), unchanged. That flow opens a
  diff-style review on top of a list rather than navigating to
  a full editor; the modal pattern is right for that
  interaction.

- **Backend is unchanged.** The CRUD endpoints already supported
  what the routed editor needs. This is the architectural
  payoff of preserving the API surface through every stage —
  the front-end could re-architect without touching the
  contract.

### Tests — 10 new (all frontend)

**`RuleEditorPage.test.tsx`:**

- `/rules/new` renders in create mode (no rule fetch fires).
- `/rules/:ruleId/edit` fetches the rule by id and pre-fills
  the name input.
- Save on a custom rule PATCHes `/rules/{id}` with the right
  body shape.
- Create on `/rules/new` POSTs `/rules` with the right body.
- Back button navigates to `/rules` without saving (PATCH
  never fires).
- Escape key navigates to `/rules` without saving.
- Built-in rule renders the read-only banner; Save button is
  NOT present.
- Built-in rule's Name input is disabled.
- Built-in rule's "Duplicate as custom rule" CTA POSTs
  `/rules/{id}/duplicate`.
- Rule-not-found path renders the empty state with a back
  link.

The list-page navigation wiring is covered by the existing
Stage 24 and Stage 29 tests — they pass unchanged with the
navigate change because the test infrastructure exercises the
hook-level interactions (apiPost / apiPatch) rather than DOM
walk-throughs.

### Test counts

- Backend: **631/631 pass** (unchanged; no backend modifications)
- Frontend: **126/126 pass** (+10 from 116)
- Combined: **757/757**

### Stage 24 ledger fully closed

For the second time in this migration sequence, an entire stage
ledger drains to zero. Stage 23's ledger closed at Stage 28
(re-probe + quarantine + bulk-optimize). Stage 24's ledger
closes now with built-in rules (Stage 29) + routed editor
(Stage 30).

The reusable-primitive economy continues: the routed editor
ships on existing primitives — `PageHeader`, `Card`, `Button`,
`Pill`, `EmptyState`, `LoadingState`, the `.rule-tab-strip` from
Stage 24, the `.settings-input` form fields from Stage 22. The
only new CSS class is `.rule-editor-shell` and that's used
purely as a structural anchor (no actual styles attached to it —
the class is reserved for if future layout work needs it; today
the page renders fine without it). Total CSS budget grew ~0.1
KB.

### Deferred-stages ledger update

- **Stage 23**: ✅ ✅ ✅ (closed at Stage 28)
- **Stage 24**: ✅ ✅ (closed at Stage 30)
- **Stage 25**: plugin upload from UI; plugin uninstall
  mechanism; plugin install from gallery; soft enable/disable
  plugin state.
- **Stage 26**: codec / container filter on Files; daily
  severity snapshot store.
- **Bug-hunt 1**: dedupe `Field` / `Input` into shared
  primitives; migrate the four ad-hoc modal wrappers to the
  Stage 22 `.dialog-*` vocabulary; add Integrations dialog
  regression test.

## [1.14.0] — 2026-05-12

Stage 29: Closes a Stage 24 ledger item — **built-in rules concept
and seeding**. A fresh Auditarr installation now ships with a
curated set of audit rules already in place; an empty dashboard
on day one is no longer the default. Operators can disable any
builtin they don't want or duplicate one to get a writable custom
variant, but they cannot rename, edit the body, or delete the
codebase-owned definitions.

### Added — backend

- **`Rule.is_builtin: bool`** column on the `rules` table.
  Indexed because the Rules page filter ("show only built-in" /
  "show only custom") drives a list query keyed on it. Default
  ``False`` for legacy and operator-created rules.

- **Migration `0014_rule_is_builtin.py`** — `batch_alter_table`
  for SQLite compatibility; creates `ix_rules_is_builtin`
  separately.

- **`app/rules/builtin.py`** module:
  - **`BuiltinRule`** frozen dataclass holding name,
    description, priority, and definition.
  - **`BUILTIN_RULES`** — a curated tuple of 7 entries:
    "Orphaned files", "Unknown video codec", "Legacy video codec
    (MPEG-2 / MPEG-4 Part 2)", "Very high bitrate (>40 Mbps)",
    "Missing subtitles (English audio)", "Very small media file
    (<10 MB)", and a placeholder "Probe failed" that ships
    disabled until the DSL grows a probe-failed predicate.
  - **`DISABLED_BY_DEFAULT`** — frozenset of names that ship in
    disabled state.
  - **`register_builtin_rules(session)`** — idempotent seeding.
    First call inserts; subsequent calls refresh codebase-owned
    fields (description + definition) on existing builtins but
    NEVER clobber operator-controlled fields (`enabled`,
    `priority`, `last_evaluated_at`, `last_match_count`). Returns
    a counters dict (`inserted` / `refreshed` / `unchanged` /
    `conflicts`) useful for startup logs and tests.

- **Startup wiring** in `app/main.py` lifespan: after settings
  overrides load, before plugin `on_startup` fires. Wrapped in
  `try/except` — a failure to seed is logged as a warning but
  doesn't abort boot.

- **API protections** in `app/api/v1/rules.py`:
  - **`GET /rules?is_builtin=true|false`** — list filter.
    `None` (default) returns the union.
  - **`PATCH /rules/{id}`** — on a builtin, rejects rename /
    description / definition with 422 + a `forbidden_fields`
    array; accepts `enabled` and `priority` (the legitimate
    per-installation tuning knobs).
  - **`DELETE /rules/{id}`** — on a builtin, returns 422 with
    "Cannot delete a built-in rule. Disable it instead."
  - **`POST /rules/{id}/duplicate`** — always produces a copy
    with `is_builtin=false`, including when duplicating a
    builtin. That's the "duplicate as custom rule" UX.
  - **`GET /rules/bundle/export?include_builtins=false`**
    (default) excludes builtins from the bundle. Every install
    seeds the same builtins at startup, so exporting them would
    just generate collision noise on re-import.
  - **`POST /rules/bundle/import`** — refuses to overwrite a
    builtin even under `on_conflict=overwrite`. The codebase
    owns the canonical definition; an operator-supplied
    overwrite would be transient (next startup re-seeds). The
    bundle's outcome lists it as "skipped" with a clear error
    rather than silently swallowing.

- **`RuleRead.is_builtin: bool`** surfaced in API responses.

### Added — frontend

- **`useRules(filters?)`** hook signature extended. Optional
  `{ is_builtin: boolean }` becomes a query param. The Custom
  tab still calls `useRules()` (no filter, returns the union)
  and excludes builtins client-side — same data path as before,
  just with a different filter step. The new Built-in tab calls
  `useRules({ is_builtin: true })`.

- **`Rule.is_builtin?: boolean`** added to the type
  (optional for forward compat with older API responses).

- **Built-in tab** on the Rules page. Renders the same
  `<RuleRow>` component as the Custom tab; the row branches on
  `rule.is_builtin` to flip its affordances:
  - Adds a "Built-in" pill badge next to the rule name.
  - Row click is a no-op (cursor stays default; clicking
    doesn't open the editor — builtin rules are read-only).
  - Delete button is disabled with title "Built-in rules can't
    be deleted. Disable instead."
  - Duplicate becomes the primary CTA with title "Duplicate as
    a custom rule (the copy is writable)".
  - Enabled-toggle stays operational — the legitimate
    per-installation knob.

- **Tab strip** now has three tabs: Custom · Built-in ·
  Suggestions. The Custom tab count reflects only custom rules;
  the Built-in tab count reflects only builtins.

- **No search box on the Built-in tab.** The builtin set is
  small (7 today, expected to stay under 20) and scrolling
  works. We can revisit if the set grows.

### Honest scope notes

- **The "Probe failed" builtin ships disabled.** Its match
  predicate isn't expressible in the current DSL (there's no
  `probe_failed` field on `SUPPORTED_FIELDS`). It's seeded so
  that when the DSL grows the field, the builtin starts firing
  automatically without needing a migration. Documented as a
  known limitation in `app/rules/builtin.py` rather than hidden.

- **No "edit builtin" UX path.** Clicking a builtin row is a
  no-op; the editor isn't opened in read-only mode. We could
  build a read-only editor, but that's a meaningful piece of UI
  scope and the "duplicate to see/edit" workflow is direct
  enough for today.

- **The Suggestions tab is unchanged.** Suggestions live
  alongside this work; surfacing "should this become a builtin?"
  is a separate question for a future stage.

- **Operator-collision logging.** If an operator created a
  custom rule named "Orphaned files" before upgrading to a
  release that ships that builtin, the seeder logs a warning
  and skips. The operator's row stays untouched; we never
  silently promote ownership.

### Bug found and fixed

Python's `logging` module reserves `name` as a `LogRecord`
attribute. Initial seeding logs `log.info("...",
extra={"name": spec.name})` raised
`KeyError: "Attempt to overwrite 'name' in LogRecord"`. Renamed
the extras key to `rule_name`. Worth flagging for future stages
that add structured logging — the codebase doesn't enforce a
naming convention for extras, but it should probably avoid
`name`, `message`, `asctime`, and the other LogRecord reserved
keys.

### Tests — 27 new (19 backend + 8 frontend)

**Backend unit (`test_rules_builtin_stage29.py`, 5 tests):**
- at_least_one_builtin_exists
- all_builtin_names_are_unique
- all_builtin_definitions_parse (every BuiltinRule validates
  against `RuleDefinition`)
- disabled_by_default_names_are_subset_of_builtins
- builtin_dataclass_is_frozen

**Backend integration (`test_rules_builtin_stage29.py`, 14 tests):**
- register_builtin_rules_inserts_on_first_run
- register_builtin_rules_is_idempotent
- register_builtin_rules_refreshes_definition (stored
  description / definition get refreshed to current codebase
  version; everything else stays put)
- register_builtin_rules_preserves_operator_enabled (operator
  disables a builtin → re-seed doesn't re-enable)
- register_builtin_rules_skips_custom_collision (operator
  creates a custom rule with a builtin's name → seed logs +
  skips, never promotes)
- list_rules_filters_by_is_builtin (filter true / false /
  default work as advertised)
- patch_builtin_rejects_rename
- patch_builtin_rejects_definition_change
- patch_builtin_accepts_enabled_and_priority
- delete_builtin_rejected
- duplicate_builtin_produces_custom_rule
- export_excludes_builtins_by_default
- export_includes_builtins_when_requested
- import_refuses_to_overwrite_builtin

**Frontend (`RulesPage.stage29.test.tsx`, 8 tests):**
- Built-in tab renders with correct count
- Custom tab count excludes builtins
- Switching to Built-in queries the right URL
  (`/rules?is_builtin=true`)
- Built-in badge renders on builtin rows
- Delete button disabled with helpful tooltip
- Duplicate button enabled with "as custom" framing
- Duplicate POSTs the right endpoint
- Enabled-toggle PATCHes only the `enabled` field

### Test counts

- Backend: **631/631 pass** (+19 from 612)
- Frontend: **116/116 pass** (+8 from 108)
- Combined: **747/747**

### Deferred-stages ledger update

- **From Stage 24**: rule editor as routed full-screen page.
  (Built-in rules concept ✅ closed this stage.)
- **From Stage 25**: plugin upload from UI; plugin uninstall
  mechanism; plugin install from gallery; soft enable/disable
  plugin state.
- **From Stage 26**: codec / container filter on Files; daily
  severity snapshot store.
- **From Bug-hunt 1**: dedupe `Field` / `Input` into shared
  primitives; migrate the four ad-hoc modal wrappers to the
  Stage 22 `.dialog-*` vocabulary; add Integrations dialog
  regression test.

### Notes

- **Zero new CSS** this stage. The "Built-in" pill uses
  `<Pill sev="info">`; the tab strip uses the existing
  `.segmented` class; the row uses the same `.files-table` and
  `.rules-row` primitives as the Custom tab.

- **The reusable-primitive economy continues to compound.**
  Stage 29 ships a new UX (built-in vs custom distinction with
  protected affordances) entirely on existing CSS. The row
  component now branches on a flag, but the visual vocabulary
  is unchanged.

## Bug-hunt 1 — 2026-05-12

Pre-Stage-22 page audit. The five operational surfaces that
hadn't been touched since the modernization began (Automation,
Optimization, Integrations, Notifications, Help) got a focused
stability + a11y pass. **No backend changes, no version bump.**
Three classes of real bugs were found and fixed; cosmetic legacy
(ad-hoc Field/Input duplicated 4 times, dialog wrappers not on
the Stage 22 `.dialog-*` family) was identified but deferred —
this is a stability stage, not a refactor.

### Fixed — error states no longer swallowed (5 cards)

Five list cards previously fell through from the loading branch
straight to the empty-state branch on API errors, lying that
"no data exists" instead of surfacing the failure:

- **OptimizationPage** → queue card
- **AutomationPage** → recent runs card
- **AutomationPage** → optimization queue card
- **NotificationsPage** → channels card
- **NotificationsPage** → recent deliveries card

Each now branches to `<ErrorState>` with the actual error message
before the empty-state check. Profiles card on the Optimization
page already had the correct pattern; the others did not. This
matters operationally because a database outage or worker crash
would manifest as a silent "queue is empty" message — the
operator wouldn't know to investigate.

### Fixed — optimization queue polled forever

`useOptimizationQueueDetail` had `refetchInterval: 5_000` —
hard-coded to fire every 5 seconds regardless of state. The
inline comment claimed "while there is active work" but the code
didn't check. Real impact: every viewer of the Optimization page
hammered the queue endpoint indefinitely, even when everything
was idle.

Fix: the `refetchInterval` is now a function that returns `5_000`
only while there are `running` or `queued` items, and `false`
otherwise. React Query halts the timer in the `false` case and
resumes refetching only when a mutation invalidates the query
(e.g. the operator clicks "Run next" or a new item is enqueued
from the Files page). The 5s cadence during active work is
preserved — progress bars still feel live.

### Fixed — four dialogs missing a11y attributes

Four modal dialogs across the four un-modernized pages had no
`role="dialog"`, no `aria-modal="true"`, and no
`aria-labelledby`. Screen readers and assistive tools couldn't
identify them as modals.

Fixed dialogs:
- Automation → schedule create
- Integrations → connect
- Notifications → channel create
- Optimization → profile create / edit

Each now carries:
- `role="dialog"` on the panel
- `aria-modal="true"` on the panel
- `id="…-dialog-title"` on the `<h3>` title
- `aria-labelledby` on the panel pointing at the title

The Files-page drawer (Stage 23) and Rules dialog (Stage 24)
already had these; the four older dialogs didn't.

### Identified but NOT fixed (cosmetic, not bugs)

- **Duplicate `Field` / `Input` definitions across 4 pages.** Each
  page redefined the same ~22 lines. This is code-hygiene noise,
  not a stability issue. Belongs in a future refactor stage.

- **Four ad-hoc dialog wrappers** using
  `fixed inset-0 z-40 bg-black/40 flex items-center justify-center p-4`
  instead of the Stage 22 `.dialog-backdrop` / `.dialog-body` /
  `.dialog-foot` family. Visual drift is real but minor; this
  belongs in a future modernization stage covering the four
  un-touched pages.

A bug-hunt stage should produce a delta in operational
reliability, not a cosmetic refactor; these stay on the ledger
for a future modernization stage rather than ballooning this
one's scope.

### Tests — 9 new (all frontend)

**`features/BugHunt1.test.tsx`:**

- OptimizationPage surfaces queue-fetch errors (does NOT show
  empty-state when API errors).
- AutomationPage surfaces runs-fetch errors (does NOT show "no
  runs yet" when API errors).
- AutomationPage surfaces optimization-queue errors.
- NotificationsPage surfaces channels-fetch errors.
- NotificationsPage surfaces deliveries-fetch errors.
- OptimizationPage profile dialog has all three a11y attrs.
- AutomationPage schedule dialog has all three a11y attrs.
- NotificationsPage channel dialog has all three a11y attrs.
- Optimization queue does NOT issue extra fetches after settling
  (regression test for the polling-forever bug; verifies that
  with all items in completed state, no additional GET fires
  within a sub-second observation window).

The Integrations dialog test was deliberately omitted from the
suite for now — its `Discover` flow has more setup ceremony than
the other three, and adding test coverage there is its own piece
of work. The fix itself is in place and exercised by manual
verification; future Integrations work will pick up the test.

### Test counts

- Frontend: **108/108 pass** (+9 from 99)
- Backend: unchanged (no backend modifications this stage)
- Combined: **720/720**

### Notes

- This is the first non-version-bump stage in the sequence
  because no API contracts changed and no backend code was
  touched. Frontend operational behavior is more honest about
  failures but the externally-observable API surface didn't
  shift.

- Deferred-stages ledger update — bug-hunt 1 adds:
  - **From Bug-hunt 1**: dedupe `Field` / `Input` into shared
    `components/ui/Field` and `components/ui/Input` primitives;
    migrate the four ad-hoc modal wrappers to the Stage 22
    `.dialog-*` vocabulary.

- The Integrations dialog has the a11y fix applied but lacks a
  pinning test. If a future stage modernizes that page (likely
  alongside or before the ad-hoc-dialog cleanup), the test
  should be added then.

## [1.13.0] — 2026-05-12

Stage 28: Closes the **last** Stage 23 ledger item — bulk-optimize
endpoint + profile picker. The Files-page selection-bar Optimize
button, disabled-with-tooltip since Stage 23, is now live. The
full Stage 23 lifecycle (bulk re-evaluate, bulk re-probe, bulk
quarantine, bulk optimize) is operational end-to-end.

### Added — backend

- **`POST /api/v1/optimization/bulk-enqueue`** — admin-only. Body:
  `{media_ids: [...up to 500], profile: "name"}`. Route registered
  between `/enqueue` and `/run-next` so the literal-paths-first
  rule (FastAPI route matching) is preserved.

  Per-bucket response:
  - `queued`: pair didn't exist; newly added to the queue.
  - `already_queued`: pair was already in `queued` state;
    `queued_at` is refreshed but no new row.
  - `skipped_active`: pair was in `running`/`completed`/`failed`/
    `cancelled`/`skipped`; left alone. The operator can use the
    Retry button on the Optimization page to re-queue these.
  - `files_not_found`: ids that didn't resolve to a media row.

  Failure modes:
  - Profile name unknown → 404 (the whole request fails; we
    don't want to silently pick another profile).
  - Profile is disabled → 422 (disabled profiles won't run; an
    operator queueing against them would build up a stale
    backlog).
  - Duplicate ids in `media_ids` → 422 (consistent with every
    other bulk endpoint).
  - `media_ids` empty list → 422 (Pydantic schema
    `min_length=1`).
  - Non-admin → 403.

- **`OptimizationBulkEnqueueRequest`**, **`OptimizationBulkEnqueueResponse`**
  in `app/schemas/optimization.py`.

### Added — frontend

- **`useBulkEnqueueOptimization()`** hook — POST to
  `/optimization/bulk-enqueue`, invalidates the
  `["optimization"]` query key on success.

- **`BulkEnqueueOptimizationResult`** type matching the
  four-bucket server response.

### Wired — Files page selection bar

- **Optimize button is now a profile picker.** Replaces the
  Stage 23/27 disabled placeholder. Clicking the button opens a
  popover (`.popover` family, Stage 23 vocabulary — no new CSS)
  listing enabled profiles. Choosing a profile fires the
  bulk-enqueue request.

- **Three button states:**
  - Loading: profiles query in flight — disabled with "Loading
    profiles…" tooltip; no layout shift.
  - Empty: no enabled profiles — disabled with "No enabled
    optimization profiles — create one on the Optimization page
    first."
  - Has profiles: enabled; toggles the popover open/closed.

- **Disabled profiles are hidden from the picker.** Server
  rejects them with 422; surfacing them and failing at click time
  would be a foot-gun.

- **Popover dismisses on:** profile selection, Escape, click
  outside the picker container. Same interaction shape as the
  Stage 23 column-visibility menu — no new pattern for operators
  to learn.

- **Four-bucket toast** on completion (e.g. "8 queued, 2 already
  queued, 1 skipped (in progress)"). Tone shifts to "warn" when
  any items were skipped or not found.

### Honest scope notes

- **No retry-from-bulk-skip flow.** When the bulk endpoint
  reports `skipped_active`, the operator must visit the
  Optimization page and use Retry on those items. Building a
  bulk-retry-from-Files would require either a second mutation
  inside the picker (clobber UX) or chained server-side semantics
  that conflate "queue" and "retry". Today's separation is
  cleaner — Retry is a deliberate decision per item, not a
  silent batch step.

- **No per-file profile picking.** The picker chooses ONE profile
  for the entire selection. Selecting "Movies + Shrink HEVC" and
  "Anime + Re-encode AV1" in a single bulk operation would need
  per-row profile state and would muddy the queue's
  (file, profile) uniqueness contract. Operators can iterate:
  filter to movies, bulk-enqueue HEVC; filter to anime,
  bulk-enqueue AV1.

- **The bulk endpoint runs sequentially** (one media-id loop body
  at a time). With the 500-item cap and SQLite's per-statement
  cost, this completes well under a second on realistic
  selections. We're not parallelizing because each iteration
  does a `SELECT` + conditional `upsert_queued` on the same
  session; concurrent writes would just contend on the connection.

- **No background queue dispatch.** The bulk endpoint queues;
  the worker dispatches. Same separation as the existing
  `/enqueue` endpoint. The "run now" path is unchanged.

### Tests — 15 new (9 backend + 6 frontend)

**Backend (`tests/integration/test_optimization_stage28.py`):**

- Happy path: 3 files queued cleanly.
- Idempotent on second call: 2 files reported `already_queued`,
  no duplicate rows in the DB.
- Skips active items: a pre-seeded `completed` (file, profile)
  pair stays put.
- Partial: known ids + one unknown id → `files_not_found`
  surfaces the bad id.
- Unknown profile → 404 (no rows touched).
- Disabled profile → 422 with explicit message.
- Non-admin → 403.
- Duplicate ids in request → 422.
- Empty `media_ids` → 422 (Pydantic schema validation).

**Frontend (`features/files/FilesPage.stage28.test.tsx`):**

- Clicking Optimize opens a popover listing enabled profiles
  only (the disabled "Old Profile" fixture does NOT appear).
- Choosing a profile POSTs to `/bulk-enqueue` with the right
  body shape.
- Popover dismisses after a profile is chosen.
- Popover dismisses on Escape.
- Optimize button disabled when no enabled profiles exist; the
  title points the operator at the Optimization page.
- Optimize button disabled when the profiles list is empty
  entirely.

### Test counts

- Backend: **612/612 pass** (+9 from 603)
- Frontend: **99/99 pass** (+6 from 93)
- Combined: **711/711**

### Notes

- The Stage 23 ledger is now **fully closed**:
  - ✅ Stage 27: per-file scanner re-probe entrypoint
  - ✅ Stage 27: quarantine state in data model
  - ✅ Stage 28: bulk-optimize endpoint + profile picker

- Deferred-stages ledger update:
  - **From Stage 24**: rule editor as routed full-screen page;
    built-in rules concept and seeding.
  - **From Stage 25**: plugin upload from UI; plugin uninstall
    mechanism; plugin install from gallery; soft enable/disable
    plugin state.
  - **From Stage 26**: codec / container filter on Files;
    daily severity snapshot store.

- The reusable-primitive economy continues to pay off: zero new
  CSS this stage. The `.popover`, `.popover-head`, `.popover-row`,
  `.files-selection-bar`, and `Button` variants did all the work.
  The picker is functionally novel UX (profile selection from a
  selection bar) but visually it's the column-visibility menu
  again.

## [1.12.0] — 2026-05-12

Stage 27: First deferred-work pickup stage. Closes three Stage 23
ledger items in one pass — per-file scanner re-probe entrypoint,
quarantine state in the data model, and wiring of the Stage 23
disabled Re-probe and Quarantine buttons. The bulk-optimize
button stays disabled as planned (that's Stage 28). This is the
first stage that explicitly answers "what was deferred?" rather
than modernizing a UI surface.

### Added — backend

- **`POST /api/v1/media/{id}/reprobe`** — admin-only. Re-runs
  ffprobe on a single file's path without a full library scan.
  Use case: operator notices stale metadata or a probe that
  failed mid-scan and wants to refresh just one entry.

  Three branches, each returning 200:
  - File exists, probe succeeds → probe columns overwritten,
    `probe_failed`/`probe_error` cleared, `seen_at` bumped.
  - File exists, probe fails → `probe_failed=true`, error
    recorded, **prior probe data preserved** (some data is
    better than no data).
  - File missing on disk → `is_orphaned=true`, no probe attempt
    (saves IO), row returned as-is. The endpoint doesn't 404
    because the operator just asked us to check, and "the file
    is gone" is itself the answer they need.

- **`POST /api/v1/media/bulk/reprobe`** — admin-only. Sequential
  loop over up to 500 media ids. Concurrency is bounded by
  FfprobeService's internal semaphore (currently 4). Response
  separates outcomes into four buckets: `files_reprobed`,
  `files_failed`, `files_orphaned`, `files_not_found`. Partial
  failures don't fail the batch.

- **`POST /api/v1/media/{id}/quarantine`** + bulk variant — both
  admin-only. Marks a file as deliberately out-of-scope.
  Idempotent at the row level: re-quarantining a quarantined file
  refreshes the timestamp + reason rather than erroring. Optional
  free-text reason capped at 512 chars; emits
  `media.quarantined` event for downstream listeners.

- **`POST /api/v1/media/{id}/unquarantine`** + bulk variant —
  both admin-only. Restores a quarantined file. Idempotent: a
  no-op on files that aren't quarantined (keeps bulk operations
  clean when the selection mixes states).

- **`Scanner.reprobe_one(media_file)`** — new public method on
  the scanner service. Handles the success / failure / orphan /
  cleared-orphan-on-reappearance branches. Emits `media.reprobed`
  with `{id, ok, orphaned}` regardless of outcome. Module-level
  invariant: re-evaluating rules is **not** chained from this
  method. Separating "refresh probe data" from "recompute
  severity" matches the existing layering (scan → probe; rules
  service → re-evaluate) and lets the operator decide which to do
  next.

- **`PluginLoader._failed_loads` carry-over note**: nothing
  changed here; the field continues to surface failed plugin
  loads. Mentioned only because this stage adds analogous
  honest-state surfacing on the media side (`probe_failed`
  + `is_orphaned` + `quarantined` together compose the file's
  operational state).

### Added — backend data model

- **Three new columns on `media_files`:**
  - `quarantined: bool` — defaults `false`, indexed (the default
    Files-page query excludes quarantined files; the index
    keeps that fast).
  - `quarantined_at: datetime?` — timestamp the operator
    quarantined this file. NULL when not quarantined.
  - `quarantined_reason: str?` — optional free-text reason
    (max 512 chars).

- **Migration `0013_quarantine.py`** — uses `batch_alter_table`
  for SQLite compatibility. Index `ix_media_files_quarantined`
  created separately for portability.

- **`MediaFilter.quarantined: bool | None`** on the media
  repository. `None` means "no filter" (return both);
  `False` means "exclude quarantined" (the page default); `True`
  means "only quarantined" (the audit view).

### Added — list endpoint behavior

- **`GET /api/v1/media` defaults to excluding quarantined**
  files. New query params:
  - `quarantined=true` → only quarantined (audit view)
  - `quarantined=false` → only non-quarantined (explicit; same
    as default)
  - `include_quarantined=true` → both (data-export style query)

  The tri-state was deliberate: a single boolean toggle couldn't
  express the operationally-useful "only quarantined" review
  surface where the operator decides what to release.

### Added — frontend hooks

- `useReprobeMedia()`, `useQuarantineMedia()`,
  `useUnquarantineMedia()` — single-file mutations.
- `useBulkReprobe()`, `useBulkQuarantine()`,
  `useBulkUnquarantine()` — selection-bar mutations.
- All six invalidate the media list query on success. The
  single-file variants also invalidate the per-file detail.
- New result types: `BulkReprobeResult`, `BulkQuarantineResult`,
  `BulkUnquarantineResult`.
- `MediaFileSummary.quarantined?: boolean` — surfaced in the
  list so the table can render badges without per-row detail
  fetches.
- `MediaFileDetail.quarantined_at?`, `.quarantined_reason?` —
  audit fields, only meaningful when quarantined.
- `MediaFilters.quarantined?`, `.include_quarantined?` — the
  list-query params.

### Wired — Files page

- **Stage 23's disabled Re-probe button is now live.** Calls
  bulk-reprobe with the selected ids; toast surfaces the
  four-bucket outcome breakdown (e.g. "8 re-probed, 2 failed,
  1 orphaned"). Tone shifts to "warn" when failures or orphans
  show up.

- **Stage 23's disabled Quarantine button is now live.**
  Prompts for an optional reason via `window.prompt` (a confirm
  dialog would gate the action on extra clicks; `prompt` lets
  the operator just hit Enter for "no reason given"). Cancelling
  the prompt aborts the action.

- **Optimize button stays disabled** with an updated title
  reading "...(Stage 28)" so the deferred state is explicit.

- **Quarantine view-mode dropdown** in the filter toolbar
  (Hide / Include / Quarantined only). Tri-state because the
  three views serve different operator needs and a boolean
  couldn't express them.

- **Quarantined pill in the table** alongside the orphan icon
  in the filename cell. Pinned to the row so it travels with
  the data even in column-hidden / re-sorted views.

### Wired — File detail drawer

- **Re-probe button** in the drawer foot — runs the single-file
  reprobe endpoint, surfaces the three outcome cases via toast
  (clean / failed-probe / orphaned).

- **Quarantine / Restore toggle** in the foot — renders
  "Quarantine" when the file is clean, "Restore" when the file
  is already quarantined. The button choice reflects the
  freshest detail-fetch data so clicking Quarantine immediately
  flips the foot to Restore.

- **Quarantined badge** in the drawer head, alongside the
  severity / category / orphaned pills.

- **Quarantine reason** rendered in italics below the head pills
  when present. Lets the operator see context without leaving
  the drawer.

### Honest scope notes

- **No rule-re-evaluation chained from reprobe.** Refreshing
  probe data and recomputing severity are two operations the
  operator may want in either order or only one of. Keeping
  them separate matches the existing layering and lets the
  Files page selection bar chain them at the UX level (Re-probe
  → Re-evaluate is two clicks; you don't have to wonder which
  was implicit).

- **No persistent-audit-log entry for quarantine actions.** The
  `media.quarantined` event fires (subscribed-to via the bus by
  any future telemetry plumbing), but Auditarr doesn't have a
  per-file audit table today, and inventing one for this stage
  would be scope creep. The `quarantined_at` timestamp on the
  row is the durable record.

- **No "release after N days" or expiry semantics.** Quarantine
  is operator-driven, not time-driven. If we add it later it'll
  be a runtime setting rather than a column.

- **Optimize button still disabled.** That's the next Stage 23
  ledger item, picked up by Stage 28.

### Tests — 34 new (21 backend + 13 frontend)

**Backend unit (`tests/integration/test_scanner_reprobe_stage27.py`):**

- `reprobe_one` overwrites probe columns on success (canonical
  happy path; old `mp4`/`hevc` data replaced with new
  `matroska`/`av1`).
- `reprobe_one` failed probe preserves existing data (prior
  good `matroska`/`h264` columns remain after stub returns
  `ok=False`).
- `reprobe_one` missing file marks orphan (stub never called —
  saves IO).
- `reprobe_one` clears orphan when file reappears.
- `reprobe_one` bumps `seen_at` so subsequent library scans
  don't mistake a fresh reprobe for an orphan candidate.
- `reprobe_one` emits `media.reprobed` event.

**Backend integration (`tests/integration/test_media_stage27.py`):**

- `/{id}/reprobe` updates probe columns.
- `/{id}/reprobe` admin-only (403 for regular user).
- `/{id}/reprobe` 404 for unknown id.
- `/{id}/reprobe` orphan branch (file missing → row marked
  orphan, endpoint still 200).
- `/bulk/reprobe` happy path with two files.
- `/bulk/reprobe` partial — known id + unknown id together.
- `/bulk/reprobe` rejects duplicate ids (400).
- `/{id}/quarantine` sets state + audit fields.
- `/{id}/quarantine` idempotent (re-quarantine refreshes
  reason rather than erroring).
- `/{id}/unquarantine` clears state cleanly.
- `/{id}/quarantine` admin-only (403 for regular user).
- `GET /media` default excludes quarantined.
- `GET /media?quarantined=true` returns only quarantined.
- `GET /media?include_quarantined=true` mixes both.
- `/bulk/quarantine` + `/bulk/unquarantine` round-trip.

**Frontend (`features/files/FilesPage.stage27.test.tsx`):**

- Re-probe button POSTs to `/media/bulk/reprobe`.
- Quarantine button prompts for reason and POSTs to
  `/media/bulk/quarantine`.
- Quarantine prompt cancellation aborts the action (no POST).
- Optimize button stays disabled.
- Default list view excludes quarantine params from the URL.
- "Quarantined only" view sends `quarantined=true`.
- "Include quarantined" view sends `include_quarantined=true`
  but NOT `quarantined=`.
- Quarantined pill renders on quarantined rows when visible.

**Frontend (`features/files/FileDetailDrawer.stage27.test.tsx`):**

- Re-probe button POSTs to `/media/{id}/reprobe`.
- Quarantine button prompts and POSTs to `/media/{id}/quarantine`.
- When file is quarantined: head shows reason + foot shows
  Restore (not Quarantine).
- Restore button POSTs to `/media/{id}/unquarantine`.
- Quarantine prompt cancellation aborts the action.

### Test counts

- Backend: **603/603 pass** (+21 from 582)
- Frontend: **93/93 pass** (+13 from 80)
- Combined: **696/696**

### Notes

- The full backend test suite couldn't complete inside a single
  pytest invocation in the workspace (it's ~5+ minutes), but
  every subset was verified individually:
  - 302 unit tests pass
  - 42 media + scanner integration tests pass
  - 73 dashboard + rules + plugin integration tests pass
  - 24 auth + security integration tests pass
  - 21 new Stage 27 tests pass
  - The remaining ~140 integration tests (automation,
    optimization, notifications, etc.) are in files this stage
    doesn't touch.

- Deferred-stages ledger update:
  - **From Stage 23**: ✅ per-file scanner re-probe entrypoint;
    ✅ quarantine state in data model. **Remaining:**
    bulk-optimize endpoint + profile picker.
  - **From Stage 24**: rule editor as routed full-screen page;
    built-in rules concept and seeding.
  - **From Stage 25**: plugin upload from UI; plugin uninstall
    mechanism; plugin install from gallery; soft enable/disable
    plugin state.
  - **From Stage 26**: codec / container filter on Files;
    daily severity snapshot store.

## [1.11.0] — 2026-05-12

Stage 26: Dashboard modernization. Adds a real "Categories" card
sourced from probed metadata Auditarr already has (codec /
container breakdowns), a window-range toggle for the trend
sparklines (7d / 30d / 90d), library-card drill-down into
filtered Files, and converts the Recent scans / Recent automation
runs cards to the Stage 23 `.files-table` vocab. Honest scope
note: the prototype's "Top transcoded files" and "Codec × Device
matrix" panels are NOT shipped — Auditarr has no play-tracking
subsystem, and faking those panels would violate the project's
no-invented-data discipline.

### Added — backend

- **`GET /api/v1/dashboard/categories?limit=N`** — admin and
  non-admin readable (composition isn't privileged audit data).
  Returns up to `limit` rows per group (default 12, max 50),
  ordered by total size descending within each group. Currently
  ships two groups: `video_codec` and `container`. Both
  dimensions live in a single response because they're cheap
  aggregations and the UI renders them together; future groups
  (audio_codec, resolution, extension) can be added without a
  contract bump.

  NULL values — files the scanner couldn't probe — collapse into
  a single `unknown` row per group. A non-trivial `unknown` count
  is a useful operator signal that the probe stage is failing on
  some part of the library; the row stays in the response rather
  than being filtered out.

- **`DashboardStats.categories()`** — service method backing the
  endpoint. Two grouped queries (one per dimension) with
  `SUM(size_bytes)` ordering. Each query is bounded by `limit` so
  the worst case is two `O(limit)` aggregations regardless of
  library size — even at very large libraries the round-trip
  stays under a few milliseconds.

- **`CategoryBreakdown` dataclass** in
  `app/services/dashboard/stats.py`, plus `CategoryBreakdownRead`
  in `app/schemas/dashboard.py`. Both added to the module
  `__all__` so the names are discoverable.

### Added — frontend

- **`useDashboardCategories(limit)`** hook — `useQuery` with 60s
  staleTime (composition changes slowly relative to severity /
  scan churn).

- **`CategoriesCard.tsx`** — sectioned by `group`. Per-group bars
  are sized relative to the group's total size (not the
  library-wide total), so the Container section's bars stay
  readable even when the cumulative bytes are smaller than the
  Video codec section. `unknown` rows render with an "unprobed"
  badge and a muted icon, visually distinguishing them from
  successfully-probed buckets.

- **`RangeToggle.tsx`** — 7d / 30d / 90d segmented control in
  the page header. Drives the existing `useDashboardSeries(days)`
  hook with a different `days` parameter — no backend change
  needed; the endpoint already accepted arbitrary 1-90 day
  windows. Delta math in the Open Issues tile scales with the
  selected range: 30d compares vs a 7d prior average, 90d
  compares vs 14d. 7d window suppresses the delta entirely
  because a 2-3 day baseline is too jittery to be useful.

### Changed — frontend

- **`DashboardPage.tsx`** — Stage 23's `.files-table` vocab now
  drives the Recent scans and Recent automation runs cards.
  Denser, scannable, and matches the rest of the operational
  surfaces. Each row carries the same status pill / tag
  vocabulary used elsewhere. No new CSS — pure reuse.

- **Library card rows** are now anchor elements linking to
  `/files?library_id=<id>`. Closes the dashboard → operations
  drill-down loop alongside the existing severity drill-down on
  the heatmap.

- **`FilesPage.tsx`** — extends the Stage 14.1 deep-link useEffect
  to also honor `?library_id=<id>` from the URL. Mirrors the
  existing severity-via-URL pattern; minimal scope.

### Added — CSS primitives (Stage 26 vocabulary)

- **`.segmented.segmented-sm`** — smaller-padding modifier for
  header-level segmented controls. Generic enough for any future
  page header that needs a compact toggle (range, scope, mode).
- **`.categories-card-body`**, **`.categories-group`**,
  **`.categories-group-label`**, **`.categories-row`** family —
  the bar-list layout for the Categories card. Group separators
  are dashed borders (vs the solid borders used inside cards)
  to read as a structural boundary inside a single card rather
  than as separate cards.

### Honest scope notes

- **No "top transcoded files" card.** The prototype's design
  shows files ranked by play count over the last 30 days. That
  requires a play-tracking subsystem Auditarr doesn't have —
  there's no service watching Plex sessions or counting requests
  per-file. Inventing a fake count or scraping a guesswork proxy
  would be exactly the kind of mock data the project discipline
  forbids. The data model doesn't carry it; the panel doesn't
  ship.

- **No "codec × device transcode matrix."** Same constraint:
  device telemetry isn't in Auditarr's purview. The
  matrix-rendering CSS would be straightforward; the data isn't.

- **No new daily-snapshot store for severity.** The dashboard's
  sparklines still operate on what the existing
  `DashboardStats.series` returns. Severity-over-time trends
  would benefit from a per-day rollup table; that's its own
  stage. Current behavior — drawing the available data,
  suppressing sparklines when the series is flat — is preserved.

- **Codec drill-down is not wired up.** Categories rows display
  the data, but clicking a codec row doesn't navigate to Files
  filtered by that codec — Files has no codec filter today
  (just library / severity / category / search). Adding a codec
  filter is its own stage. Container drill-down has the same
  constraint.

### Tests — 17 new (7 backend + 10 frontend)

**Backend (`tests/integration/test_dashboard_stage26.py`):**

- `categories` returns both `video_codec` and `container` groups
- Within `video_codec`, rows are sorted by `total_size_bytes` desc
- NULL container values collapse to a single `unknown` row
- The `limit` parameter caps results per group independently
- Empty library returns `[]` cleanly (no crash on aggregation)
- Endpoint requires auth (401 for anonymous)
- Endpoint is non-admin readable (auditors need composition)

**Frontend (`features/dashboard/Stage26.test.tsx`):**

- CategoriesCard renders both group sections with their rows
- `unknown` rows render with the `unprobed` badge
- Empty composition shows the empty state without crashing
- Failed request shows the error state
- RangeToggle reports the active option via `aria-checked`
- RangeToggle's `onChange` fires with the new range
- Library rows link to `/files?library_id=<id>`
- Range toggle in header switches the underlying series query
- Recent scans render in a `<table role="grid">` (not card rows)
- Recent automation jobs render with formatted durations

### Test counts

- Backend: **582/582 pass** (+7 from 575)
- Frontend: **80/80 pass** (+10 from 70)
- Combined: **662/662**

### Notes

- The "categories" endpoint is intentionally NOT cached on the
  backend. The two queries are fast enough on realistic library
  sizes (the `media_files` table is indexed on `video_codec`),
  and a cache would complicate the invalidation story (a scan
  completion should refresh composition, but we'd then need to
  bus-publish cache invalidation from the scan completion path
  — not worth the complexity for sub-millisecond aggregations).

- The Stage 26 dashboard is the first surface to use the
  `segmented-sm` modifier. Stage 27+ headers that need a compact
  toggle (e.g. a "scope" toggle in the Files header, or a
  status filter in Optimization) can pick this up without
  adding new CSS.

- Deferred-stages ledger update — Stage 26 adds:
  - **From Stage 26**: codec / container filter on the Files
    page (would enable Category card drill-down); daily severity
    snapshot store (would unlock severity-over-time sparklines).

- The migration ledger continues to track:
  - **From Stage 23**: bulk-optimize endpoint + profile picker;
    per-file scanner re-probe entrypoint; quarantine state in
    data model.
  - **From Stage 24**: rule editor as routed full-screen page;
    built-in rules concept and seeding.
  - **From Stage 25**: plugin upload from UI; plugin uninstall
    mechanism; plugin install from gallery; soft enable/disable
    plugin state.

## [1.10.0] — 2026-05-12

Stage 25: Plugins-page modernization. Promotes the plugin surface
out of Settings into a dedicated `/plugins` page in the sidebar,
adds operator-friendly status visibility (loaded / errored /
failed_to_load) with inline error messages, and ships a real
reload-without-restart flow. The loader gets a small but meaningful
contract extension: it now tracks the state of plugins it
discovered but couldn't load, so operators see "broken" rows
alongside healthy ones instead of having to grep the log.

### Added — backend

- **`POST /api/v1/plugins/{id}/reload`** — admin-only. Reloads a
  single plugin from disk without restarting the host:
  - existing instance runs `on_shutdown` → `on_unload`
  - module is dropped from `sys.modules` so the next import
    re-reads the source from disk (the canonical use case is
    "operator edited the plugin's backend.py and wants the new
    code live")
  - manifest is re-read, in case the operator changed metadata
  - `register()` + `on_load()` run fresh
  - the new summary entry is returned so the caller sees whether
    the reload succeeded
  - 404 for unknown plugin id (i.e. a plugin that was never on
    disk in this process's lifetime — distinct from a known plugin
    that failed to load, which IS reloadable)

  Caveat documented in the endpoint docstring and worth restating:
  routes mounted during the original load CANNOT be unregistered at
  runtime (FastAPI doesn't support route removal). Reloading swaps
  the in-memory module so the existing route handlers pick up code
  changes, but adding/removing routes still needs a process restart.

- **Enriched `GET /api/v1/plugins`** — summary entries now carry:
  - `description` (from manifest) — was always loaded, never surfaced
  - `author` (from manifest) — same
  - `status`: `"loaded"`, `"errored"`, or `"failed_to_load"`
  - `last_error`: the most recent error message captured for that
    plugin (`null` when nothing has gone wrong)
  - `has_settings`: mirrors `manifest.settings`, so the UI can
    decide whether to render a Configure button per row

  Existing fields are unchanged. The response shape stays
  `list[dict[str, Any]]` (not a Pydantic model) so future
  summary fields don't force a contract bump.

- **`PluginLoader._failed_loads`** — new internal map tracking
  manifests that were discovered on disk but couldn't load. Surfaced
  via `list_summary` with `status: "failed_to_load"` and the error
  message inline. This closes the gap where a broken plugin would
  silently disappear from the operator's view after a load attempt.

- **`PluginLoader.reload_one(plugin_id)`** — the underlying
  reload primitive. Handles the four cases:
  - plugin currently loaded → tear down, re-import, re-run `on_load`
  - plugin currently failed-to-load → re-attempt the load (operator
    just fixed the issue)
  - plugin currently failed-to-load and STILL fails → record the
    new failure, return the failed summary
  - unknown plugin id → return `None` (router translates to 404)

- **`LoadedPlugin.last_error`** — new field on the loader wrapper.
  Set by `_load_one` on `on_load` failure and by `_run_lifecycle`
  on subsequent hook failures. Surfaces directly in summaries.

### Added — frontend hooks

- **`useReloadPlugin()`** — mutation that POSTs `/plugins/{id}/reload`
  and invalidates the plugins list query on success so the UI
  refreshes the status pill / last_error.
- **`PluginStatus`** type — `"loaded" | "errored" | "failed_to_load"`.
- Enriched **`PluginSummary`** — picks up `description`, `author`,
  `status`, `last_error`, `has_settings`.

### Added — frontend components

- **`PluginsPage.tsx`** — new top-level page mounted at `/plugins`.
  Tabbed Installed / Gallery view with counts in tab labels.
  Installed tab uses the Stage 23 `.files-table` vocab; rows show
  a letter-monogram badge, name + author + description, type pill,
  version, status pill, capability tags, and a per-row Reload
  button (always available) plus Configure (only when
  `has_settings`).

  Below the table, a **Lifecycle errors panel** renders only when
  at least one plugin has `status="errored"` or `"failed_to_load"`.
  Each errored row shows the status pill, name, id@version, and
  the full `last_error` message in a monospace block. The panel's
  framing — "isolated · host continues" — surfaces the existing
  load-isolation guarantee.

  Gallery tab renders the existing `/plugins/gallery` response in
  the same table layout, with categories and a Source ↗ link
  (read-only; install is deferred work — see notes below).

- **`PluginSettingsDialog.tsx`** — extracted from the old
  `SettingsPage` inline dialog. Same persisted-then-default seeding
  behavior (still gated by the `seeded` flag to prevent the
  empty-payload re-seed loop), now wrapped in Stage 22's
  `.dialog-*` primitives. Renders the plugin's `last_error` at the
  top of the body when present, so operators configuring a broken
  plugin see what's wrong without leaving the dialog.

### Added — routing & navigation

- **`/plugins`** route mounted in `AppRoutes.tsx` before the
  plugin-page wildcard so the listing wins specificity.
- **"Plugins" entry** added to the sidebar (`nav.ts`), between
  Notifications and Settings.

### Added — CSS primitives

- **`.plugin-monogram`** — 28×28 letter badge in the first table
  column. Named generically; can be adopted in Stages 26+ for
  integration / notification rows.

### Changed

- **`SettingsPage.tsx`** — the `PluginsCard` and embedded
  `PluginSettingsDialog` are **removed** entirely. Plugins live in
  exactly one place now (the `/plugins` route). No legacy
  duplicate, no half-migration — same discipline as Stages 22-24.
  219 lines of dead code dropped.

- **`PluginSummary.routes`** type widened from `string[]` to
  `boolean | string[]` to match the backend's actual response
  (the loader returns the manifest's `routes: bool`).

### Honest scope notes

- **No plugin upload from the UI.** The existing model is "drop
  into the directory and restart"; switching to upload would mean
  file handling, zip extraction, security gates around plugin
  origin, and trust signaling — none of which is "just a UI
  improvement". Future work.

- **No plugin uninstall.** Same shape — today a plugin is removed
  by deleting the directory. A real uninstall flow needs a
  decision about whether to delete settings, audit traces, and
  capability registrations. Future work.

- **No install-from-gallery.** Gallery remains browse + Source-↗
  read-only. Installing a plugin from a URL implies the same
  trust gates as upload, plus signature verification. Future work.

- **No soft enable/disable without unload.** The current contract
  is binary (loaded vs not), and inventing a third "enabled but
  inactive" state would be a substantial loader-contract change,
  not a UI improvement. The Reload button gives operators the
  edit-and-reactivate flow that "soft disable" would have served.

### Tests — 24 new (14 backend + 10 frontend)

**Backend unit (`tests/unit/test_plugin_loader_stage25.py`):**

- `list_summary` carries description, author, has_settings
- `list_summary` marks `errored` when `on_load` raises
- `list_summary` includes failed-to-load entries
- `list_summary` orders loaded/errored before failed-to-load
- `reload_one` picks up source changes (the canonical use case)
- `reload_one` recovers a failed-to-load plugin after the operator
  fixes the underlying file
- `reload_one` returns `None` for unknown plugin id
- `reload_one` records a new failure when the reload itself fails
- `reload_one` drops the module from `sys.modules` (pinned
  explicitly because it's the linchpin of the edit-reload flow)

**Backend integration (`tests/integration/test_plugin_stage25.py`):**

- `GET /api/v1/plugins` returns the enriched fields
- `POST /api/v1/plugins/{id}/reload` returns the new summary
- Reload on unknown plugin → 404
- Reload is admin-only (403 for non-admin)
- End-to-end: rewrite backend.py to fail, reload, see `failed_to_load`
  status with the error message

**Frontend (`features/plugins/PluginsPage.test.tsx` + smoke test):**

- Mount smoke test in `test-pages.test.tsx`
- Every plugin renders in the installed table
- Status pills reflect the enriched `status` field
- Tab strip switches Installed / Gallery
- Search filters the installed plugin list
- Reload button POSTs to `/plugins/{id}/reload`
- Configure button only renders when `has_settings: true`
- Lifecycle errors panel renders when there's at least one
  errored / failed plugin
- Lifecycle errors panel is absent when every plugin is loaded
- Empty state when no plugins are installed

### Test counts

- Backend: **575/575 pass** (+14 from 561)
- Frontend: **70/70 pass** (+10 from 60)
- Combined: **645/645**

### Notes

- The reload module-cache invalidation works because we use
  `importlib.util.spec_from_file_location` with a deterministic
  module name (`auditarr_plugin_{id_underscored}`). Dropping
  that entry from `sys.modules` forces the next import to re-read
  the file. This is pinned by the
  `test_reload_one_drops_module_from_sys_modules` test so a
  future refactor that changes the module-naming convention or
  the import path can't silently break the reload flow.

- The "errored" status preserves the existing load-isolation
  guarantee: a plugin whose `on_load` raises is still considered
  loaded in the sense that its `register()` ran and any
  capabilities it claimed during register remain registered. Only
  subsequent lifecycle hooks (`on_startup`, `on_shutdown`,
  `on_unload`) are skipped — see `_run_lifecycle`'s
  `_auditarr_lifecycle_failed` guard.

- The lifecycle errors panel is an example of where the prototype's
  design genuinely improves the operator experience. The previous
  Settings-tab implementation had no way to surface "this plugin
  failed to load" without the operator reading the application
  log. The promoted page + status pill + inline error message
  closes that gap with no backend churn beyond the
  `last_error` / `_failed_loads` tracking.

- Deferred-stages ledger update:
  - **From Stage 23**: bulk-optimize endpoint + profile picker;
    per-file scanner re-probe entrypoint; quarantine state in
    data model.
  - **From Stage 24**: rule editor as routed full-screen page;
    built-in rules concept and seeding.
  - **From Stage 25**: plugin upload from UI; plugin uninstall
    mechanism; plugin install from gallery; soft enable/disable
    plugin state.

## [1.9.0] — 2026-05-12

Stage 24: Rules-page modernization. Continues the Stages 22 / 23
pattern — preserve operational architecture, evolve the visual
language, surface backend capability that was previously buried.
The 562-line monolith ``RulesPage.tsx`` is decomposed into three
focused files; the dialog editor is restyled around the Stage 22
``.dialog-*`` primitives without changing its functional shape.
Backend gets three additive endpoints (duplicate / export / import)
designed to be content-addressable so two instances can converge on
the same rule set from the same bundle.

### Added — backend

- **`POST /api/v1/rules/{id}/duplicate`** — admin-only. Creates a
  disabled copy of an existing rule with a collision-resolving name
  (`{name} (copy)`, then `(copy 2)`, `(copy 3)`, … up to 100, then a
  timestamp suffix as the bail). Copies start disabled by design:
  duplicating is overwhelmingly the precursor to divergent edits,
  and shipping the divergent rule live without inspection is the
  failure mode we want to prevent. The original is untouched.

- **`GET /api/v1/rules/bundle/export`** — non-admin readable.
  Returns every rule's `(name, description, enabled, priority,
  definition)` as a portable bundle with `version: "1"` and
  `exported_at`. Volatile per-instance state (`id`, timestamps,
  `last_evaluated_at`, `last_match_count`) is deliberately excluded
  so two instances importing the same bundle land identical rules —
  the bundle is content-addressable by `(name, definition)`. Path
  intentionally lives under `/bundle/` to avoid colliding with the
  existing `GET /{rule_id}` route.

- **`POST /api/v1/rules/bundle/import`** — admin-only. Takes a
  `RuleExportBundle` plus an `on_conflict` strategy
  (`skip | rename | overwrite`):
  - **skip**: existing rule stays untouched
  - **rename**: imported rule is created with a `{name} (imported)`
    suffix alongside the existing one (default; safest)
  - **overwrite**: existing rule's definition / description /
    priority / enabled are replaced, but the rule keeps its `id`
    and any associated evaluation history — `rule_evaluations` is
    FK'd to `rule.id`, and treating overwrite as delete-then-create
    would orphan that history
  Validation is per-entry: a bundle that mixes good and bad rules
  imports the good ones and reports the bad ones as
  `action: "error"` outcomes rather than failing the whole batch.
  Repeated names within a single bundle get renamed under all
  strategies — a bundle that names two rules "Twins" should land
  both, not one. Rejects unknown bundle versions with 422.

- **New schemas** in `app/schemas/rules.py`:
  `RuleExportBundle`, `RuleExportEntry`, `RuleImportRequest`,
  `RuleImportOutcome`, `RuleImportResponse`.

### Added — frontend hooks

- **`useDuplicateRule()`** — server-side duplicate. The name is
  computed server-side; the UI doesn't try to predict it.
- **`useExportRules()`** — `useQuery` with `enabled: false` so the
  caller triggers it explicitly. In practice the Rules page calls
  `apiClient` directly for the one-shot semantics.
- **`useImportRules()`** — invalidates the rules list on success so
  imported rules show up immediately.
- **New types**: `RuleExportBundle`, `RuleExportEntry`,
  `ImportConflictStrategy`, `RuleImportOutcome`, `RuleImportResponse`.

### Added — frontend components

The 562-line `RulesPage.tsx` is decomposed into three focused files:

- **`RulesPage.tsx`** — the new shell. Tabbed Custom / Suggestions
  surface with counts in the tab labels; search box over rules;
  Import / Export / New rule actions in the toolbar; library
  Evaluate dropdown in the page header (unchanged from Stage 15
  in semantics, just relocated). The rules table uses Stage 23's
  `.files-table` vocabulary — sortable column headers, hover row,
  click-to-edit. Columns: enable switch, name+description,
  derived severity, action types, priority, matches, last eval,
  per-row Duplicate / Delete actions.

  Severity in the table is **derived** from each rule's
  `set_severity` actions (highest-rank wins, matching the
  evaluator's behavior). When a rule has no `set_severity` action,
  the column renders `—`. The frontend's `SEV_RANK` map mirrors
  the backend's `SEVERITY_LEVELS` and also accepts the legacy
  `warning` / `critical` aliases that user-imported rules
  sometimes carry.

- **`RuleDialog.tsx`** — extracted from the old monolith. Same
  Visual / Dry-run / JSON tab structure, same `useRuleVocabulary`
  + `useDryRunRule` wiring, same JSON-text-mirror behavior as
  Stage 15. The dialog chrome is rebuilt around Stage 22's
  `.dialog-backdrop` / `.dialog` / `.dialog-head` / `.dialog-body`
  / `.dialog-foot` primitives; a new `.dialog-wide` modifier
  gives this dialog enough horizontal room for the visual
  builder. The submit button lives in the dialog foot but the
  form's `onSubmit` is in the body — they're connected by
  `form.requestSubmit()`, which keeps both Enter-to-submit and
  click-to-submit working.

  Not changed: the underlying functionality. Operators see the
  same builder, dry-run flow, and validation behavior they did
  in Stage 15. A routed full-screen editor (matching the
  prototype's "rule as page" flow) is its own stage —
  architectural changes (URL state, deep-linking) shouldn't ride
  in on a visual-modernization stage.

- **`ImportRulesDialog.tsx`** — paste-or-upload bundle editor with
  conflict strategy selector and a per-rule outcome list that
  appears after submission. The list shows the `final_name`,
  flags renames (`was {original}`), and surfaces per-entry errors
  inline — so the operator sees exactly what happened to each
  rule. The strategy radio's helper text changes per choice to
  explain the trade-offs without making them dig into docs.

### Added — CSS primitives (Stage 24 vocabulary)

- **`.dialog.dialog-wide`** — wider dialog cap for editors that
  need the room (rule editor today; future plugin / integration
  configuration dialogs will use it too).
- **`.rules-toolbar` / `.rules-toolbar-search`** — toolbar above
  the table, mirrors `.files-toolbar` so the two pages read as a
  family.
- **`.rules-table-toggle` / `.rules-row-actions`** — column widths
  for the switch and action cells.
- **`.rules-row.is-disabled`** — dimmed-but-actionable styling for
  disabled rules: every cell except the actions is 65% opacity,
  so the row reads "off" at a glance but the operator can still
  toggle / duplicate / delete without fighting the dim.
- **`.rule-tab-strip` / `.rule-tab.is-active`** — underline-style
  section tabs used inside the rule editor dialog body. A
  different visual than the `.segmented` control on purpose:
  segmented controls live in toolbars; underline tabs live inside
  cards / dialogs where there's already a border framing them.

### Changed

- Old `RulesPage.tsx` is replaced. The Visual builder
  (`VisualRuleBuilder.tsx`) is unchanged — it's already a clean
  module that handles the vocabulary-driven typed inputs.

### Honest scope notes

- The prototype splits rules into Custom / Built-in / Suggestions.
  Auditarr's data model has no "built-in" distinction — every rule
  is user-created or suggestion-deployed, both stored in the same
  `rules` table. Inventing a "built-in" concept just to mirror the
  prototype would have meant a new column, a migration, and a
  preloading mechanism — that's its own stage. Stage 24 ships the
  two real tabs (Custom / Suggestions) and treats Built-in as
  future work.

- The rule editor remains a dialog rather than the prototype's
  full-screen routed page. URL-state for in-progress edits, deep
  linking, breadcrumbs, and unsaved-changes detection across
  route transitions are all worth doing — and are all
  architectural enough to warrant their own stage.

- Duplicate is a server-side endpoint rather than a client-side
  fetch-and-create. The name-collision logic lives in one place
  (the backend) and aligns with how the existing suggestion-deploy
  endpoint handles the same problem.

### Tests — 24 new (15 backend + 9 frontend)

Backend (`tests/integration/test_rules_stage24.py`):

- Duplicate creates a disabled copy with the right name
- Three duplicates of the same rule yield `(copy)`, `(copy 2)`,
  `(copy 3)` — exercising the increment loop
- Duplicate unknown rule → 404
- Duplicate non-admin → 403
- Export returns a portable bundle without volatile state
- Export is non-admin readable (backup / replication needs)
- Import creates new rules when no collisions
- Import skip strategy preserves the existing rule
- Import rename strategy creates with a suffix
- Import overwrite preserves the rule's ID (so eval history isn't
  orphaned)
- Import repeats within a single bundle get renamed (no unique-
  constraint crash)
- Import reports invalid entries inline without failing the batch
- Import rejects unknown bundle versions (422)
- Import admin-only
- Round-trip preserves definitions (export → delete → import →
  identical rules)

Frontend (`features/rules/RulesPage.test.tsx`):

- Both rules render in the table by default
- Tab strip switches between Custom and Suggestions
- Search filters the rules table
- Duplicate row action POSTs to `/rules/{id}/duplicate`
- Export button fetches the bundle
- Import button opens the import dialog
- Import dialog submits the bundle with the chosen strategy and
  shows per-rule outcomes
- Toggle switch on a row PATCHes the rule's enabled state
- Derived severity column shows the highest-rank `set_severity`

### Test counts

- Backend: **561/561 pass** (+15 from 546)
- Frontend: **60/60 pass** (+9 from 51)
- Combined: **621/621**

### Notes

- The rules export bundle's `version: "1"` is a coarse compat tag,
  not the app version. Future bumps are reserved for incompatible
  shape changes; importers MAY reject bundles whose version they
  don't recognize.

- Auditarr's `rule_evaluations` FK is the reason the import
  overwrite strategy mutates rules in place rather than replacing
  them. An "overwrite means delete + create" implementation would
  orphan every prior evaluation row for the affected rule, which
  is exactly the operational data operators care about preserving
  ("which files matched this rule, last time?").

- The CSS primitives shipped here are generic — `.dialog-wide`,
  `.rule-tab-strip`, the toolbar pattern — and ready for Stages 25
  (Plugins) and 26 (Dashboard / Integrations / Notifications)
  without scope-shaping.

- Future stages already scheduled (per migration ledger):
  - Bulk-optimize endpoint + profile picker (deferred from Stage 23)
  - Per-file scanner re-probe entrypoint (deferred from Stage 23)
  - Quarantine state in data model (deferred from Stage 23)
  - Rule editor as routed full-screen page (deferred from Stage 24)
  - Built-in rules concept and seeding (deferred from Stage 24)

## [1.8.0] — 2026-05-12

Stage 23: Files-page modernization. Picks up the same pattern Stage
22 established for Settings — preserve operational architecture,
adopt the prototype's interaction language — and applies it to the
indexed-files surface. The rewritten page replaces the previous
CSS-grid table with a real sortable table, adds row selection and
bulk re-evaluate, exposes the rule-evaluation log per file in a
slide-in detail drawer, and persists column visibility / sort to
localStorage. Backend gets three additive endpoints; nothing is
broken or repurposed.

### Added — backend

- **Sortable column on `GET /api/v1/media`** — new `sort` and
  `sort_dir` query params. The repository enforces a whitelist of
  eight sortable columns (`path`, `filename`, `size_bytes`,
  `mtime`, `severity_rank`, `category`, `extension`, `seen_at`);
  every sortable column is already indexed or denormalized, so the
  whitelist is also a "don't ORDER BY a JSON blob" safety net.
  Unknown columns fall back to the legacy severity-first order
  rather than 422'ing — the UI sometimes pre-emptively passes
  through a sort key, and breaking the listing for that case
  would be worse than degrading to the default order. `sort_dir`
  is `asc|desc` (pattern-validated). Every sorted query also
  carries a secondary ORDER BY on `path` so two rows with
  identical primary values come back deterministically — critical
  for offset pagination, otherwise the same row can flicker
  between pages on adjacent requests.

- **`GET /api/v1/media/{id}/evaluations`** — per-file rule
  evaluation listing. The `rule_evaluations` table has held this
  data since Stage 6 ("the Files page detail panel — why is this
  file flagged?" was literally the comment); this stage finally
  exposes it. The response shape is `MediaEvaluationRead` —
  `RuleEvaluationRead` enriched with `rule_name` and
  `rule_enabled` so the drawer doesn't need a second round-trip
  per row. Rows for disabled or deleted rules are still returned
  (with `rule_enabled: false`); they represent the file's
  historical evaluation state, which is what the drawer should
  show. Non-admin readable — the rule-evaluation log is operational
  visibility, not a write surface.

- **`POST /api/v1/media/bulk/reevaluate`** — admin-only bulk path
  that re-runs the enabled rule set against a specific set of
  files. Max 500 ids per request (matches the list endpoint's
  page-size ceiling so a single bulk request can never select
  more files than a single page could surface). Rejects
  duplicates explicitly with 422 — silent de-dup would more often
  signal an aggregation bug in the caller than a deliberate
  intent. Returns `{files_evaluated, files_not_found}` so the UI
  can report partial success without surfacing exceptions.
  Admin-gated because re-evaluation mutates `rule_evaluations` and
  the file's denormalized `severity` / `severity_rank` — matches
  the existing gate on `POST /api/v1/rules/libraries/{id}/evaluate`.

### Added — frontend hooks

- **`useMediaDetail(id)`** — single-file fetch backing the detail
  drawer. 30s staleTime matches `useLibraries`; refetchOnWindowFocus
  off because the probe blob is large and operators don't want
  every alt-tab to re-pull it.
- **`useMediaEvaluations(id)`** — the per-file evaluation list,
  same caching profile.
- **`useBulkReevaluate()`** — mutation that POSTs the selected
  IDs and invalidates `["media"]` on success. Wide invalidation
  rather than surgical because re-evaluation can change severity
  on any subset of files; a partial refresh would risk showing
  a stale severity for a file the operator just re-checked.

### Added — frontend types

`MediaFileDetail`, `MediaEvaluation`, `MediaSortKey`,
`BulkReevaluateResult`. All strictly typed against the backend
schemas; the page never accepts `any` for inbound payloads.

### Added — Files preferences store

**`stores/filesPrefsStore.ts`** — new zustand store, persisted to
`auditarr.files.prefs` in localStorage. Holds visible columns,
page size, and current sort. Kept separate from `uiStore.ts`
(global theme/accent prefs) because page-local state shouldn't
crowd the global namespace.

The store enforces two invariants on rehydrate AND on every
`setVisibleColumns` / `toggleColumn` call:

- the `always: true` columns (currently just `filename`) can't
  be hidden, so a stale persisted state from a future release
  that removed the always-marker can never strand the operator
  on a column-less table;
- unknown column keys (a future release that removes a column
  entirely) are dropped silently, so the table never tries to
  render a phantom column.

### Added — frontend components

- **`FilesPage.tsx`** — full rewrite. Real `<table>` with
  sortable column headers, `aria-sort` for screen readers,
  checkbox selection with select-all-on-page (indeterminate
  state when partial), row click opens detail drawer (with
  checkbox cell `stopPropagation` so toggling a checkbox
  doesn't open the drawer). Preserves the Stage 14.1 scope bar
  verbatim — vocabulary, deep-link severity filter, chip
  toggles — because that's a different concern and rewriting it
  would have been gratuitous churn. The library / category /
  search filters move into a single toolbar row above the table
  alongside the column-visibility menu and a `shown / total`
  count.

- **`FileDetailDrawer.tsx`** — slide-in detail panel triggered by
  a row click. Renders metadata grid (size, resolution, codecs,
  container, duration, bitrate, subtitles), audio/subtitle
  language tracks (when known), matched rules with severity
  pills and an "inactive" marker when the matched rule has
  since been disabled, and the raw ffprobe JSON in a
  copy-to-clipboard panel. Escape-to-close + backdrop click.
  Activity log is **deliberately not shown** — there is no
  per-file audit table in the data model today; the prototype
  mocks one, but adding it here would have been the "merge
  mock data into production" failure mode the planning
  directive prohibited.

- **`ColumnVisibilityMenu.tsx`** — reusable popover. Always-
  required columns render as disabled-checked rows with a
  "required" sub-label so the operator can see what's locked in,
  rather than just hiding them from the menu (the prototype's
  pattern). Click-outside and Escape-to-close. Built on the
  new `.popover` primitives.

### Added — CSS primitives (deliberately reusable)

Same naming discipline as Stage 22 — generic names, not
files-page-specific, so Stage 24+ can adopt them. Appended to
`frontend/src/styles/components.css`:

- **`.popover` / `.popover-head` / `.popover-row` / `.popover-foot`**
  — anchored menu. Column visibility today; Stage 24's rule-builder
  filter menus and Stage 25's plugin-options menus will use them.
- **`.files-toolbar` / `.files-toolbar-search`** — one-row strip
  above the table. Same 28px control height as `.settings-input`
  so adjacent controls align.
- **`.files-selection-bar`** — surface-sunk inline bar with the
  selection count + bulk actions. Slots into the toolbar.
- **`.files-table` + `.files-table-row.is-selected` +
  `.files-table-sort-ind` + `.files-checkbox`** — dense table
  with sticky header, sortable column hover, selected-row tint
  (accent-soft), and aria-sort-aware indicators.
- **`.files-pager`** — sticky-feel table footer.
- **`.file-drawer` / `.file-drawer-backdrop` / `.file-drawer-head` /
  `.file-drawer-body` / `.file-drawer-foot` / `.file-drawer-section` /
  `.file-meta-grid` / `.file-meta-cell` / `.file-probe-pre`** —
  slide-in detail panel. The drawer vocab mirrors the Stage 22
  dialog vocab (`.dialog-head` / `.dialog-body` / `.dialog-foot`)
  in name and geometry so they're easy to scan together.

### Changed

- `useMedia.ts` — `MediaFilters` extended with `sort` and
  `sort_dir`. Backward-compatible: every existing caller
  continues to work without those fields.

### Honest scope notes

The selection bar renders Optimize / Re-probe / Quarantine as
**disabled buttons with explanatory tooltips**. They're shown so
the design intent is visible, but they don't fire because:

- *Optimize* would need an optimization-profile picker AND a
  bulk-enqueue endpoint on the existing optimization queue.
- *Re-probe* would need a new scanner entrypoint that takes a
  list of files rather than a library root (the scanner today
  walks library trees).
- *Quarantine* would need a new file state in the data model
  plus migration plus scan-loop changes.

Each is a defensible follow-up stage; none meets this stage's
"only ship what's honest" bar.

The detail drawer's Activity log section is similarly omitted —
no per-file audit table exists, so mocking entries would be the
exact "no UI-derived operational state" violation Stage 22's
directive called out.

### Tests — 21 new (13 backend + 8 frontend)

Backend (`tests/integration/test_media_stage23.py`):

- sort by size ascending returns results in ascending order
- sort by filename descending returns descending order
- unknown sort column gracefully falls back (no 422)
- `sort_dir` pattern validation 422s on garbage values
- per-file evaluations returns rule names + enabled flags
- per-file evaluations 404 for unknown file id
- per-file evaluations preserve rows for disabled rules
- bulk re-evaluate updates files (and the 4-file fixture
  exercises the end-to-end path through the rules service)
- bulk re-evaluate reports unknown ids without failing
- bulk re-evaluate rejects duplicate ids
- bulk re-evaluate rejects empty list
- bulk re-evaluate rejects oversized list (>500)
- bulk re-evaluate non-admin 403

Frontend (`features/files/FilesPage.test.tsx`):

- rows render once the media list resolves
- clicking a sortable header rewrites the API query with
  `sort=size_bytes&sort_dir=desc`; second click flips to `asc`
- checking a row reveals the selection bar with the right count
- select-all-on-page header checkbox toggles every visible row;
  aria-label flips appropriately
- Re-evaluate rules POSTs `media_ids` containing the selected
  set
- clicking a row opens the detail drawer with the file's
  filename in the dialog heading
- column visibility menu toggles a previously hidden column
  ("Updated") into the table
- changing a filter clears the current selection (prevents
  applying bulk actions to files no longer visible)

### Test counts

- Backend: **546/546 pass** (+13 from 533)
- Frontend: **51/51 pass** (+8 from 43)
- Combined: **597/597**

### Notes

- The prototype's Files view influenced this stage's vocabulary
  but is not the implementation — same approach as Stage 22.
  Where the prototype had decorative buttons (Optimize / Re-probe
  / Quarantine), this stage either ships them as disabled
  placeholders or omits them entirely rather than mock the
  backend. Where the prototype had real interaction patterns
  (column visibility, multi-select, sort, detail drawer), this
  stage adopts them on real backend data.

- The CSS primitives shipped here are explicitly generic.
  `.popover`, `.file-drawer-*`, `.files-table` + `.is-selected`,
  and `.files-selection-bar` are named for reuse — Stage 24 (Rules)
  needs a similar selection bar; Stage 25 (Plugins) needs the
  popover for option menus; Stage 26 (Dashboard) can adopt the
  drawer geometry for metric drilldowns.

- The Stage 22 directive's "preserve existing operational
  features" rule was honored: the scope bar, severity deep-link
  from the dashboard, scan-progress pill, run-scan button,
  library/category/search filters, and "no libraries" empty state
  all remain. Only the table itself was rewritten.

## [1.7.0] — 2026-05-12

Stage 22: schema-driven Settings UI. Wires the Stage 21 runtime-
settings + encrypted-secrets backends into a real operator-facing
editor, and adds a per-integration path-mappings editor. Frontend-
focused — the only backend touch is the version bump. The visual
direction comes from the design prototype reviewed during planning:
category rail, dense field cards, sticky save bar, confirm-diff
dialog. The implementation reuses the existing token system and
component primitives so the new panels compose with the rest of the
Settings page rather than landing as a parallel UI.

### Added — runtime settings editor

- **`useRuntimeSettings()` hook** (`hooks/useRuntimeSettings.ts`) —
  the typed client for every endpoint Stage 21 shipped. Merges
  `GET /system/runtime-settings/describe` (schema) and
  `GET /system/runtime-settings` (current values) into a single
  `RuntimeField[]` so the panel reads from one shape; pattern
  constraints that look like enums (`^(debug|info|warning|...)$`)
  are auto-converted to `options` arrays so those fields render as
  `<select>` instead of free-text. Mutations
  (`useSetRuntimeOverride`, `useClearRuntimeOverride`) invalidate
  only the values query, leaving the describe schema cached for
  five minutes. Forbidden errors are detected structurally
  (`status === 403`) so the panel can render an admin-required
  state instead of a generic error.

- **`RuntimeSettingsPanel`** (`features/settings/RuntimeSettingsPanel.tsx`)
  — schema-driven editor. Left rail lists categories with a
  per-category dirty dot; right pane lists field cards for the
  active category. Each card shows the field's `key` as a
  monospace handle, label, impact pill (`immediate` / `next tick`),
  override pill when overridden, description, control, default
  hint, and a `revert` / `restore default` action. Dirty state
  flips the card border to `--accent` and tints the background.

- **Sticky save bar** appears at the panel foot when there are
  pending edits. Shows the pending count and an immediate / next-
  tick split so the operator can see which changes apply now vs.
  on the next scheduler tick. `Discard all` resets the edit map;
  `Apply N changes` opens a confirm dialog.

- **Confirm-apply dialog** renders a diff table (setting · before ·
  after · apply) and aggregates per-field `requires_warning`
  strings into a single warning callout. The apply pill on each
  row is `now` for immediate impact, `next tick` for deferred,
  `clear` when the proposed value equals the env default — the
  panel routes those to `DELETE` rather than `PUT` so the
  override table stays minimal, matching the design intent of
  Stage 21's storage model.

- **Apply semantics** — mutations fire sequentially rather than
  in parallel. Each `PUT` (or `DELETE`) triggers a Redis publish
  and an in-process apply on the backend; running them
  concurrently would interleave the side-effects in a way that's
  harder to reason about, and the volumes here (max 23 fields,
  typically 1–3 at a time) don't need the parallelism. On
  success, applied keys briefly carry an `is-applied` pulse on
  their card so the operator sees confirmation tied to the
  specific row.

### Added — encrypted-secrets editor

- **`SecretsPanel`** (`features/settings/SecretsPanel.tsx`) — one
  card per secret slot, populated from `useSecrets()` (combines
  the describe + status endpoints, same pattern as runtime
  settings). Plaintext NEVER round-trips: the panel reads only
  metadata (`has_value`, `last_set_at`, `last_tested_at`,
  `last_test_ok`, `last_test_detail`) and accepts new values via
  a one-way `PUT`. Length validation against the schema's
  `min_length` / `max_length` happens client-side before submit
  so the operator gets feedback without a round-trip.

- **Test connection** button is rendered only for slots whose
  schema entry has `has_test_handler: true`. Clicking it calls
  `POST /system/secrets/{key}/test`; the hook coerces the
  backend's 502 (upstream rejected the secret) into an in-band
  `{ ok: false, detail }` so test failures show inline as a
  warning toast rather than as an exception — that's the normal
  UX outcome of typo'd keys. Other errors (no secret stored,
  schema validation) still surface as errors.

- **Clear** requires confirmation since there's no undo; the
  operator has to paste the value again to recover.

### Added — path-mappings editor

- **`PathMappingsPanel`** (`features/settings/PathMappingsPanel.tsx`)
  — uses the `GET /system/path-mappings` aggregator + per-
  integration `PUT /system/path-mappings/{integration_id}`
  endpoints (also Stage 21). Renders one card per integration
  with a two-column from→to grid plus add/delete/save/reset.
  Save is per-integration to match the backend's PUT contract;
  there's no cross-integration bulk save because the failure mode
  ("I changed three integrations and one silently failed")
  becomes invisible. Incomplete rows (only `from` or only `to`)
  block save and surface a toast.

### Added — CSS primitives (deliberately reusable)

The Stage 22 styles in `frontend/src/styles/components.css` are
written as generic building blocks, not panel-specific selectors,
because the same patterns will style the future modernizations of
plugins, integrations, automation, and notifications:

- **`.settings-input` / `select.settings-input`** — 28px-tall
  themed form input with focus-ring and `aria-invalid` styling.
  Matches `Button.sm` height so adjacent controls align visually.
- **`.settings-switch` / `.settings-switch-thumb`** — toggle
  switch with on/off + thumb animation.
- **`.runtime-grid` / `.runtime-rail` / `.runtime-rail-item`** —
  200px category-rail + fields-list grid that collapses to a
  horizontal scrolling rail under 720px.
- **`.runtime-field` / `.is-dirty` / `.is-applied`** — field card
  with dirty-state styling and a short "applied" pulse
  (`@keyframes runtimeFieldApplied`).
- **`.runtime-warn`** — inline warning row used by dirty-field
  warnings, validation feedback, and the confirm dialog.
- **`.runtime-savebar`** — sticky panel-foot save bar.
- **`.dialog-backdrop` / `.dialog` / `.dialog-head` /
  `.dialog-body` / `.dialog-foot`** — modal primitives with
  backdrop fade and dialog slide-in animations. Replace the
  current `confirm()` callsites in later stages.
- **`.diff-table` / `.diff-head` / `.diff-row` /
  `.diff-cell` / `.diff-cell-after`** — change-preview table.
- **`.secret-card` / `.secret-card-head` /
  `.secret-card-controls` / `.secret-card-meta`** — secret
  editor card.
- **`.path-mapping-card` / `.path-mapping-rows` /
  `.path-mapping-row` / `.path-mapping-foot`** — per-integration
  mapping editor.

Naming is intentional: nothing is scoped to runtime-settings
specifically. Stage 23+ can adopt these patterns directly.

### Changed

- **`SettingsPage` wrapper width** — `max-w-3xl` → `max-w-5xl`.
  The new schema-driven panel's rail + fields grid needs ~720px
  to render at the prototype's density. The existing
  Libraries / Plugins / Appearance / SystemConfig / VirusTotal
  cards already have their own internal padding, so widening the
  outer wrapper doesn't visually stretch them; it just gives the
  Stage 22 panels enough room.

- **Forbidden / API-error detection in the new hook is
  structural** (`status === 403`, `status === 502`) rather than
  `instanceof ApiError`. The structural check survives
  `vi.mock` module replacement in tests and stays correct under
  any future refactor that swaps the error class. Production
  behavior is identical: every API rejection goes through
  `ApiError`, every `ApiError` has a `status`. The change is in
  the hook only; the public re-export of `ApiError` is preserved
  so external callers keep their `instanceof` narrowing.

- **`Icon` set gained `lock`** — used by the SecretsPanel head
  row and the admin-required empty states in the runtime and
  secrets panels.

### Tests — 5 new (frontend)

- **`features/settings/RuntimeSettingsPanel.test.tsx`** (4 tests)
  — pins the panel's operational contract:
  - schema-driven render: categories appear, field cards appear,
    the overridden-field pill shows on the right card.
  - dirty-state gating: save bar is absent at rest; appears with
    the right immediate/next-tick split after an edit; the
    schema's `requires_warning` text is absent until that field
    becomes dirty.
  - apply path: editing a non-overridden field and restoring an
    overridden field to default, opening the confirm dialog,
    confirming, and asserting the hook issues a `PUT` for the
    first and a `DELETE` for the second.
  - admin gating: when describe returns 403, the panel renders
    its admin-required empty state.

- **`test-pages.test.tsx`** (+1 assertion) — the existing
  page-mount smoke test now also asserts the Runtime settings,
  Secrets, and Path mappings panel headings render under the
  mock `apiClient.get → null` contract, catching the "panel
  blows up on undefined data" regression class.

### Test counts

- Backend: **533/533 pass** (unchanged — no backend code touched)
- Frontend: **43/43 pass** (+5 from 38)
- Alembic 0001..0012 round-trip clean (unchanged)

### Notes

- This stage delivers what the Stage 21 closing note promised
  ("the next stage redesigns the Settings page to a tabbed
  editor … and that stage will wire to the foundation shipped
  here"). The existing route + page composition is preserved
  per Stage 22's planning directive: the new panels integrate
  into the existing card stack, with editable controls (Libraries
  / Plugins / Appearance / Runtime / Secrets / Path mappings)
  sitting above the read-only Stage-20 env-driven config cards.
  A full settings-as-shell rework (left-rail nav for every
  section, prototype-style) is left for a later stage; the
  primitives shipped here will style it when it lands.

- The CSS primitives are explicitly reusable. Stage 23+ should
  prefer adopting `.runtime-field`, `.runtime-warn`,
  `.runtime-savebar`, `.dialog-*`, and `.diff-table` over
  introducing parallel selectors. Naming was kept generic for
  exactly this reason.

- The design prototype's `view-misc.jsx` enumerates several
  Settings sections that have no backend foundation today:
  Email transport, Audit log viewer, Advanced (free-form),
  About. Wiring those is out of scope here — they'd require
  backend work first and would otherwise be the kind of "mock
  data merged into production" failure mode the planning
  directive explicitly prohibited.

- Future stages that modernize Files / Rules / Dashboard /
  Integrations / Plugins / Notifications can reuse the primitives
  introduced here. The progression model from the planning
  directive ("select one operational surface, modernize it
  deeply, then move to the next") is the explicit recommendation
  for those stages.

## [1.6.0] — 2026-05-11

Stage 21: runtime-editable settings backend. The Settings UI is being
redesigned next stage; this stage builds the backend foundation so the
UI redesign can wire to real endpoints rather than mocked data. The
brief was "expose every non-secret hardcoded setting + the resource
tunables, with validation that prevents bad values from crashing the
app." Backend-only — no UI work this stage by design.

### Added — runtime overrides

- **`runtime_setting_overrides` table** (migration 0012) — single
  row per editable setting, JSON value column. Rows exist only when
  an operator has customized a value; absent rows mean "use the
  env-driven default", which keeps the table small and the
  override delta trivial to render.

- **Validation schema** (`app/core/runtime_settings_schema.py`) —
  the safety contract for runtime edits. 23 whitelisted keys
  across 9 categories (logging, auth, rate_limiting, scanner,
  updater, plugins, housekeeping, webhooks, integrations). Each
  entry pins type, range/pattern, default, impact ("immediate"
  vs "next_tick"), and an optional warning string for sharp-edge
  changes. The whitelist is enforced at every layer; anything
  not on it is restart-required and the API rejects writes with
  a 422 that names the env var the operator should edit instead.

- **23 runtime-editable fields**:
  - **Logging**: `log_level` (immediate; pushes the new level into
    the live stdlib logger).
  - **Auth**: `access_token_ttl_minutes`, `refresh_token_ttl_days`,
    `ws_require_auth`.
  - **Rate limiting**: `auth_rate_limit_attempts`,
    `auth_rate_limit_window_seconds`.
  - **Scanner** (new in this stage): `scanner_ffprobe_timeout_seconds`,
    `scanner_worker_concurrency`, `scanner_max_file_size_mb`.
  - **Updater**: `update_feed_url`, `update_check_interval_minutes`,
    `update_install_mode`.
  - **Plugins**: `plugin_gallery_url` (empty = disabled).
  - **Housekeeping**: 4 retention windows
    (`housekeeping_*_retention_days`), 0 = keep forever.
  - **Webhooks** (new in this stage):
    `notifications_webhook_default_timeout_seconds`,
    `notifications_webhook_max_retries`.
  - **Integrations / VirusTotal** (new in this stage):
    `virustotal_enabled`, `virustotal_scan_on_import`,
    `virustotal_rescan_interval_days`, `virustotal_daily_quota`.

- **Service layer** (`app/services/runtime_settings.py`) —
  `RuntimeSettingsService` handles validate → persist → apply
  in-process → publish reload. The validate step runs through a
  per-key pydantic model built from the schema entry; the DB
  write only happens if validation succeeds, so a bad value never
  makes it to disk.

- **Apply-in-process semantics** — overrides are applied directly
  to the cached `Settings` instance via `setattr`. Existing call
  sites that read `settings.foo` automatically see overridden
  values with no source change. A side-effects hook handles
  log_level (pushes into the live stdlib logger) so that change
  takes effect without a restart.

- **Hot reload across processes** via Redis pubsub channel
  `auditarr:settings:reload`. Both the API lifespan and the
  worker startup subscribe; when one process writes an override,
  the other re-reads from the DB and re-applies in-process. The
  publish call is best-effort — a Redis outage doesn't fail the
  write (the in-process change has already been applied).

- **Resilient startup** — invalid DB rows (override no longer
  passing current schema validation, or referencing a removed
  key) are logged and skipped, not crash-raised. A future release
  that tightens a range can't render an existing deployment
  unbootable.

### Added — encrypted secrets

- **`encrypted_secrets` table** (migration 0012) — same single-key
  pattern but with a `LargeBinary` ciphertext column plus audit
  fields (`set_by_user_id`, `last_set_at`, `last_tested_at`,
  `last_test_ok`, `last_test_detail`). Plaintexts are never
  returned via the API.

- **Reused AES-256-GCM via HKDF** — extended the existing
  `SecretBox` (used by integration secrets) with raw-bytes helpers
  (`encrypt_bytes` / `decrypt_bytes`). The Fernet key derives from
  `Settings.secret_key`; rotating that key invalidates every stored
  secret, which is the correct behavior (operators rotating the
  master key should re-enter API keys anyway).

- **One secret slot today** — `virustotal_api_key`, length-bound
  32..128 chars. The slot list is extensible via the same schema
  module; adding a new secret is one `SecretSpec` entry.

- **Test endpoint** — `POST /api/v1/system/secrets/{key}/test`
  probes the upstream API with the stored secret to confirm it
  works without exposing it. The VirusTotal handler hits
  `GET /users/me`, returning distinguished outcomes for 200 / 401
  / 403 / 429 / network errors. The test outcome is recorded as
  audit metadata (last_tested_at, last_test_ok, last_test_detail)
  so the UI can render "API key works" / "rate-limited" / "401
  rejected" without ever seeing the plaintext.

### Added — endpoints

Nine new routes, all admin-only except the path-mappings read
(non-admin-visible for operational debugging):

- `GET    /api/v1/system/runtime-settings/describe` — UI metadata
- `GET    /api/v1/system/secrets/describe` — UI metadata
- `GET    /api/v1/system/runtime-settings` — current effective values
- `PUT    /api/v1/system/runtime-settings/{key}` — set override
- `DELETE /api/v1/system/runtime-settings/{key}` — clear override
- `GET    /api/v1/system/secrets` — metadata only (never plaintext)
- `PUT    /api/v1/system/secrets/{key}` — store an encrypted secret
- `DELETE /api/v1/system/secrets/{key}` — clear a stored secret
- `POST   /api/v1/system/secrets/{key}/test` — probe upstream

### Added — path mappings aggregator

Path mappings live in each `Integration.config['path_mappings']`,
which is where the scanner reads them from. Adding a separate
table would split the source of truth. Instead, two new endpoints
provide a centralized view + edit path that thin-wraps the
existing data:

- `GET /api/v1/system/path-mappings` — flat list across all
  integrations, runs through the existing `parse_mappings` so the
  UI sees exactly what the scanner sees. Non-admin-visible; the
  read is operationally useful for debugging.
- `PUT /api/v1/system/path-mappings/{integration_id}` — replaces
  the mappings list on one integration. Round-trips through the
  same parser so the stored value matches what the scanner will
  read back. Validates each entry; malformed entries return 422
  rather than getting silently dropped. Other config keys
  (base_url, library_section_ids, etc.) are preserved on rewrite.

### Added — profile editing

- **`PATCH /api/v1/auth/me`** — current user updates their own
  email and/or full_name. Username and password are deliberately
  NOT exposed by this endpoint: username changes break audit-log
  attribution (separate admin tooling for that), password changes
  use the existing `POST /password/change` flow with current-
  password confirmation.
- Email changes trigger a `is_verified = False` reset (operators
  re-verify); collision with another account returns 422; empty
  string for `full_name` clears the field.
- Audit log records the field names changed (`{"fields": ["email"]}`)
  but never the values — so reading the audit log later can't
  leak the operator's previous or new email.
- Schema uses `extra="forbid"` so unknown fields (like a sneaky
  `role: admin`) return 422 instead of silently being accepted.

### Tests — 83 new

- **`test_runtime_settings_schema.py`** (26 tests) — whitelist
  gating, schema/Settings sync, range enforcement per representative
  field, plugin_gallery_url empty-is-disabled, update_feed_url
  http(s)-only, install_mode pattern, ws_require_auth boolean,
  secret length bounds, describe-payload shape.
- **`test_runtime_settings_api.py`** (13 tests) — auth gating on
  every endpoint, describe-by-category coverage, CRUD round-trip,
  422 on invalid value, 422 on unknown key with env hint,
  idempotent clear, persistence across requests.
- **`test_secrets_api.py`** (14 tests) — the big one. Asserts
  the plaintext NEVER appears in any list/set response or test-
  outcome metadata. Tested via grep on response body for the
  recognizable plaintext, and as base64. Plus length-bound
  enforcement, clear, test endpoint success + failure flows
  (monkey-patched handler), and corruption resistance (tampered
  ciphertext returns 5xx rather than silently leaking).
- **`test_path_mappings_api.py`** (10 tests) — read aggregator,
  admin-only write, mappings replace cleanly, empty-list clears,
  malformed entries return 422, unknown integration ID returns
  404, other config keys preserved on rewrite.
- **`test_auth_profile.py`** (11 tests) — partial updates leave
  other fields alone, empty full_name clears, username/password/
  role rejected as 422 (extra-forbid), email collision rejected,
  re-submitting own email is a no-op, audit log records fields
  not values.
- **`test_settings_hot_reload.py`** (9 tests) — startup-time
  apply mutates Settings, writes apply immediately, clears
  restore env defaults, log_level side effect fires on both
  write and startup paths, invalid/unknown DB rows skipped not
  raised, Redis outage doesn't fail the write.

### Test counts

- Backend: **533/533 pass** (+83 from 450)
- Frontend: 38/38 pass (unchanged — backend-only stage)
- Alembic 0001..0012 round-trip clean

### Notes

- The UI in v1.6.0 hasn't been updated to use these endpoints yet
  — that's deliberate. The next stage redesigns the Settings page
  to a tabbed editor (General / Scanning / Auth & API / Storage
  paths / Integrations / Advanced / About), and that stage will
  wire to the foundation shipped here.
- Sample value to put in `AUDITARR_SECRET_KEY` for new
  deployments: `openssl rand -base64 48`. The key length is
  critical for the secret-encryption derivation (HKDF rejects keys
  shorter than 16 chars).

## [1.5.0] — 2026-05-11

Stage 20: UI polish + Settings page expansion. Investigated the
deployed v1.0 build that the operator was running and identified five
issues. Three turned out to be stale-build artifacts (the running app
was missing the rebuilt frontend dist from Stages 14–19). The other
two were real code bugs, plus the Settings page genuinely was missing
the sections the operator wanted.

### Fixed

- **Theme toggle didn't apply after page reload.** The `zustand/persist`
  middleware rehydrates state from localStorage but the original code
  only ran `applyTheme()` inside the `setTheme` action — never after
  rehydration. So a user who toggled to dark, reloaded the page, and
  clicked the toggle button could see "store says dark, DOM says
  light, click flips store back to light, nothing visually changes".
  Fixed two ways:
  - Added an `onRehydrateStorage` callback in `uiStore.ts` that
    re-applies theme + accent to the DOM after the persist layer
    restores state from localStorage.
  - Added a `useEffect` in `AppShell.tsx` that subscribes to
    `theme` and `accent` and pushes them to the DOM on every change
    — belt-and-suspenders so future store mutations can't drift
    from the DOM.
- **Borders too contrasty.** Both the light and dark `--border`
  tokens were a touch hot. Softened from `#e6e6e3` → `#ededeb`
  (light) and `#25252a` → `#1c1c20` (dark). Border-width unchanged
  (1px is correct); just lowered the contrast.

### Added — backend

- **`GET /api/v1/system/config`** returns a structured, read-only
  view of the runtime config organized into six sections — API,
  Auth, Storage, Updater, Plugins, Housekeeping. Used by the
  Settings page to display "what's my deployment configured to do"
  without the operator SSH-ing in to read the env file. Admin-only,
  with passwords redacted from DB and Redis URLs (`auditarr:***@db`
  format — host portion still visible so operators can sanity-check
  the config). The JWT signing key is never exposed.

### Added — frontend

- **`useSystemConfig()` hook** in `hooks/useSystem.ts` mirroring the
  six sections returned by `/system/config`. Strongly typed
  (`SystemConfig`, `SystemConfigApi`, `SystemConfigStorage`, …).
  Disables retries so non-admin users don't keep hammering the 403.
- **Settings page expanded with seven new cards** (additive — no
  existing sections removed):
  - **API** — bind host/port, CORS origins, WebSocket auth flag,
    log level + format, environment label.
  - **Auth** — access token TTL, refresh TTL, login rate limit
    (attempts per window).
  - **Scanner** — explanatory card noting that scanner config is
    per-library (set on each library) rather than env-driven, and
    that the walker reuses the platform's ffprobe.
  - **Updater** — feed URL, check interval, install mode (with a
    Stage 19 install-mode hint), sentinel + status file paths.
  - **Storage paths** — DB URL (redacted), pool sizes, Redis URL
    (redacted), queue name, data/plugin/docs/frontend dirs.
  - **Plugin gallery** — gallery URL with a hint when disabled.
  - **Housekeeping** — retention windows for the four audit
    tables, with "kept indefinitely" copy when set to 0.
  - **VirusTotal** — discovery card surfacing the future plugin
    integration. Marks the feature as preview, explains where
    configuration will live (under Plugins, when the
    `virustotal` plugin ships), and lists status pills so the
    operator can see the current state at a glance. No data
    leaves the network until the plugin ships.
- Each read-only card carries a banner explaining that editing
  requires changing the env file (`.env` for Docker,
  `/etc/auditarr/auditarr.env` for bare-metal) and restarting the
  service. The banner names the relevant env-var prefix so
  operators don't have to grep around to find which knob to turn.
- Shared `ConfigRow` helper renders consistent key/value rows
  across all read-only cards: monospace values, secret-redacted
  hints, truncation with title attribute for overflow, muted
  rendering when a value is null.

### Diagnosed but not code-changes (stale frontend build)

The screenshot from the running v1.0 deployment showed three
visual issues that the workspace code already handles correctly:

- **Sidebar overlap with main content** — the Tailwind classes
  `w-sidebar`, `pl-sidebar`, `h-header` all generate properly in
  the built CSS (verified by grepping `dist/assets/*.css`). The
  overlap is a v1.0 build that never had these classes; a redeploy
  of the rebuilt `frontend/dist/` resolves it.
- **Missing color on stats / pills / tags** — `text-sev-ok`,
  `bg-sev-warn`, etc. all generate in the built CSS. The
  dashboard's `healthClass()` helper already maps statuses to
  severity classes. Same stale-build issue.
- **Sidebar nav items rendering identical to main content** — same
  root cause.

A redeploy of the v1.5.0 frontend bundle resolves all three.

### Tests

- **+8 backend tests** in `tests/integration/test_system_config.py`
  pinning the contract: requires authentication, requires admin
  role, returns all six expected sections, includes the Stage 19
  install_mode in the updater section, redacts DB password,
  redacts Redis password, never exposes the JWT secret_key or its
  fixture value.

### Test counts

- Backend: **450/450 pass** (+8 from 442)
- Frontend: 38/38 pass
- Alembic 0001..0011 round-trip clean

## [1.4.0] — 2026-05-11

Stage 19: install-environment-aware updater. Stage 18 added the
bare-metal installer but the update function was still
Docker-specific — clicking **Apply update** on a bare-metal install
wrote a sentinel file that nothing read. This release closes that
gap with a parallel update path and an install-mode contract that
guarantees the backend never fires an apply into the wrong helper.

### Added — backend

- **Install-mode detector** (`app/updater/install_mode.py`).
  Auto-detects on startup whether Auditarr is running inside Docker
  (via `/.dockerenv` + the `container=` env var + a cgroup probe),
  under systemd as installed by `install-bare-metal.sh` (via the
  combination of `/etc/auditarr/auditarr.env` AND `INVOCATION_ID`
  being set — both signals required to avoid false positives), or
  in an unknown environment (falls back to `unmanaged`).
- **`AUDITARR_UPDATE_INSTALL_MODE`** environment variable lets
  operators override detection. Values: `auto` (default), `docker`,
  `bare-metal`, `unmanaged`. Unknown explicit values fail safe to
  `unmanaged` so an apply can't fire into the wrong helper.
- **`GET /api/v1/updater/status`** now returns `install_mode` and
  `apply_enabled` (false when `install_mode == "unmanaged"`). The
  frontend uses these to render appropriate copy and disable the
  Apply button when no helper is wired up.
- **`POST /api/v1/updater/apply`** refuses with HTTP 409 + clear
  message when `install_mode == "unmanaged"`, enforced server-side
  so a malicious or buggy client can't bypass the UI gate.

### Added — bare-metal updater

- **`updater/auditarr-update-bare-metal.sh`** — counterpart to
  `docker/updater/auditarr-update.sh`. Reads the same sentinel
  protocol, downloads the release tarball from a configurable URL
  template (`%s`-substituted with the version), optionally verifies
  SHA256, snapshots the current `/opt/auditarr`, stops services,
  rsyncs new files (preserving `/etc/auditarr` and
  `/var/lib/auditarr`), re-runs `pip install -e .` to pick up new
  deps, runs `alembic upgrade head`, and restarts services. On any
  failure it restores the snapshot and brings the old services
  back up. shellcheck-clean.
- **`install-bare-metal.sh`** now installs the watcher script under
  `/opt/auditarr/updater/`, writes `/etc/auditarr/updater.env`
  (with the URL templates commented out — secure default), and
  registers a hardened `auditarr-update-watcher.service` systemd
  unit. The main env file gets `AUDITARR_UPDATE_INSTALL_MODE=
  bare-metal` pinned so detection never has to guess.
- **Bare-metal updater config is opt-in** — the operator must
  uncomment `AUDITARR_RELEASE_TARBALL_URL` in
  `/etc/auditarr/updater.env` before auto-updates work. Failing to
  do this produces a clear error in the apply-status UI rather
  than silently doing nothing.

### Added — frontend

- **`UpdaterStatus` TS type** extended with `install_mode` and
  `apply_enabled`.
- **HelpPage updates card** renders install-mode in the subtitle
  ("Installed: 1.4.0 · Bare-metal (systemd)"), labels the Apply
  button with the install mode ("Apply 1.5.0 (Docker)" /
  "Apply 1.5.0 (systemd)"), and shows a warning banner with the
  exact env var to set when `apply_enabled === false`. Apply button
  is disabled in that case with a descriptive tooltip.

### Documentation

- **`docs/getting-started/install-bare-metal.md`** gained an
  Updates section covering the manual upgrade path, the optional
  auto-update watcher, the URL template config, SHA256 verification,
  and the install-mode override. Uninstall instructions updated to
  include the new watcher unit.

### Test counts

- Backend: **442/442 pass** (+13 from 429): 11 install-mode unit
  tests covering explicit override, case insensitivity, fail-safe
  for unknown values, Docker auto-detect via marker file and env
  var, bare-metal requiring both env file AND `INVOCATION_ID`, and
  fallback behavior; 2 updater-API integration tests covering the
  new status fields and the 409 gating.
- Frontend: 38/38 pass.
- Alembic 0001..0011 round-trip clean.

## [1.3.0] — 2026-05-11

Stage 18: bare-metal installer for LXC containers and VMs. Auditarr
previously assumed Docker — fine for most deployments but a real
blocker for users running Proxmox LXC, hardened VMs, or hosts where
Docker conflicts with their network setup. This release adds a
parallel install path that uses systemd, native Postgres + Redis, and
a Python venv. No Docker required.

### Added

- **`install-bare-metal.sh`** — top-level installer for LXC / VM
  hosts. Tested on Debian 12, Ubuntu 22.04, and Ubuntu 24.04. Walks
  the operator through: system-package install (Python 3.12,
  Postgres, Redis, ffmpeg, nginx, build tools); service user
  (`auditarr`) and FHS-compliant directories (`/opt/auditarr`,
  `/etc/auditarr`, `/var/lib/auditarr`, `/var/log/auditarr`);
  application layout (backend, plugins, built frontend); Python venv
  at `/opt/auditarr/venv` with the backend installed editable;
  Postgres role + database bootstrap with a generated password;
  `/etc/auditarr/auditarr.env` generation (mode 0640, group-readable
  by `auditarr`); Alembic migrations; first admin user prompt;
  systemd units for the API (`auditarr-api.service`, gunicorn +
  uvicorn) and worker (`auditarr-worker.service`, arq) with
  `ProtectSystem=strict`, `NoNewPrivileges`, `PrivateTmp`, etc.;
  optional nginx reverse proxy on port 80 with proper WebSocket
  upgrade for `/api/v1/ws`. Idempotent — re-running it preserves
  the secret key, the admin user, and the DB credentials in the
  env file.
- **Non-interactive mode** for IaC tooling (Ansible / Terraform /
  cloud-init). Set `AUDITARR_NONINTERACTIVE=1` plus
  `AUDITARR_ADMIN_EMAIL` / `AUDITARR_ADMIN_USERNAME` /
  `AUDITARR_ADMIN_PASSWORD` and the installer runs end-to-end with
  no prompts. Other knobs (`AUDITARR_USER`, `AUDITARR_HOME`,
  `AUDITARR_LISTEN_HOST`, `AUDITARR_LISTEN_PORT`, etc.) let you
  customize the install layout per host.
- **`auditarr user count-admins`** CLI command. Prints the admin
  user count to stdout (no log noise — logs route to stderr via
  `configure_logging`) so the installer can check via `$(...)`
  capture whether a first admin needs to be created.
- **`auditarr user bootstrap-admin`** CLI command. Creates the
  first admin user; takes the password from an env var
  (`--password-from-env`) rather than the command line so it
  doesn't leak into `/proc/<pid>/cmdline` or shell history.
  Returns exit 0 on success, 2 on missing/short password, 3 on
  email/username conflict — letting the installer respond
  intelligently to re-runs.
- **`docs/getting-started/install-bare-metal.md`** — full operator
  documentation covering requirements, install flow, non-interactive
  env vars, CLI usage post-install, update path, uninstall, and
  troubleshooting.

### Changed

- **`docs/getting-started/installation.md`** — opens with a Docker /
  bare-metal split at the top so operators see both options
  immediately rather than discovering the bare-metal path by
  accident.
- **`README.md`** — quick-start gained a "Bare-metal (LXC / VM, no
  Docker)" section alongside the existing Docker flow. Repository
  layout updated to list `install-bare-metal.sh`.

### Quality

- **shellcheck clean** at warning level on `install-bare-metal.sh`.
- **Portable shell** — no GNU-only constructs. `xargs -d '\n'`
  (rejected by busybox) replaced with a `while read` loop driven by
  `grep -E`. Env-file loading uses `set -a; . file; set +a` rather
  than xargs splitting.
- **CLI logs go to stderr**, not stdout. Before Stage 18 the CLI
  commands inherited a default logger configuration that wrote to
  stdout, which would have polluted the installer's `$(...)`
  captures. The new user commands explicitly call
  `configure_logging(get_settings())` so the structlog handler
  attaches to `sys.stderr`.

### Tests

- **+7 backend tests** in `tests/integration/test_cli_user_commands.py`
  pinning the CLI contract the installer depends on: stdout-is-just-
  the-number, exit codes for each error class (2 / 3 / 0), idempotent
  re-runs, refuses duplicate email AND duplicate username. Each test
  invokes the CLI as a subprocess (the way the installer does) so
  there's no risk of in-process state leaking between tests.

### Test counts

- Backend: **429/429 pass** (+7 from 422)
- Frontend: 38/38 pass (no frontend changes)
- Alembic 0001..0011 round-trip clean

## [1.2.1] — 2026-05-11

Stage 17: polish, stability, and sanity pass after the v1.2.0 Stage
16 ship. No new features — this release pins down rough edges
identified by auditing the Stage 16 work against the rest of the
codebase, and adds defensive parsing so real-world Plex/Jellyfin
responses can't crash the poller.

### Fixed

- **`failed_playback` heuristic emitted a rule that never matched.**
  The analyzer's `failed_playback` heuristic was generating a rule
  with `match: tags contains "playback-failed"`, but the affected
  files weren't tagged that way — so deploying the rule did nothing.
  The heuristic now generates a `filename in [<list-of-affected-
  filenames>]` rule that actually flags the failing files at
  severity=error, and additionally adds a `playback-failed` tag.
  The evidence shape also gained a structured `sample` array (used
  by the review modal's Evidence tab) and a clear
  `affected_file_count` counter.
- **Plex history parser would crash on null `Player` objects.**
  Some Plex history records omit `Player` entirely; others return it
  as `null`. The original code called `.get(...)` on the result,
  which `AttributeError`'d on null. Now wrapped in a type check; the
  malformed entry is dropped and the rest of the batch processes.
- **Plex history parser would crash on string `viewedAt`.** Some
  Plex builds return `viewedAt` as a string. `int()` on a
  non-numeric string crashed the whole batch. Now routed through
  `_safe_int` which returns None on garbage, and that entry is
  dropped silently.
- **Plex history parser would crash on null `duration`.**
  `_safe_int(None) // 1000` raised `TypeError`. Fixed with an
  explicit None check after the cast.
- **Jellyfin session parser would crash on null `PlayState` /
  `TranscodingInfo`.** Same class of bug — chained `.get(...)`
  against a null field. All nested field accesses are now
  null-guarded.
- **Jellyfin session parser would crash on malformed
  `MediaStreams`.** A broken server returning non-dict entries in
  `MediaStreams` would crash `next((s for s in streams if
  s.get("Type") == "Video"))`. Now filters to dicts before
  iterating.
- **Plex/Jellyfin parsers wrap the whole translation in a try/except
  catching `(AttributeError, TypeError, ValueError, KeyError)`** so
  a single bad entry never poisons a whole batch. Bad entries are
  dropped and the rest of the polled events go through.

### Added — tests

- **+21 unit tests covering the Plex/Jellyfin parsers' robustness**
  (`tests/unit/test_telemetry_parsers.py`). Pins behavior for every
  shape of malformed payload listed above plus the well-formed
  baseline. Tests are pure parser tests — no HTTP, no DB.

### Test counts

- Backend: **422/422 pass** (+21 from 401)
- Frontend: 38/38 pass
- Alembic 0001..0011 round-trip clean

## [1.2.0] — 2026-05-11

Stage 16: data-driven rule recommendations. The product now watches
Plex and Jellyfin playback telemetry, detects recurring problem
patterns (transcodes, bitrate ceilings, container compatibility
issues, failed playbacks), and surfaces them as one-click rule
suggestions on the Dashboard. Closes the loop between "Auditarr knows
my library" and "Auditarr suggests what to do about it."

### Added — backend

- **Per-integration path remapping.** Plex and Jellyfin connector
  config schemas now expose a `path_mappings: list[{from, to}]` field
  that rewrites the paths the integration reports to how Auditarr
  indexes them on disk. Empty list = "assume 1:1". Longest-prefix
  wins, directory boundaries respected (`/data/tv` does not match
  `/data/tvshows`).
- **Drift detection.** If more than half of polled playback paths
  fail to resolve to an indexed file, the integration is flagged
  `health_status="degraded"` with a descriptive
  `health_detail` ("23 of 47 playback paths don't resolve — configure
  path mappings") and a `integration.path_drift` domain event is
  emitted. Operators see the prompt directly in the UI rather than
  silently getting no suggestions.
- **`playback_events` table** + migration 0010. One row per playback
  observation from Plex/Jellyfin. Indexed on integration_id,
  media_file_id (nullable, SET NULL on delete), started_at, and
  decision. Unique on (integration_id, upstream_id) for dedup.
- **`integration_polling_cursors` table** for resumable polling.
- **`IntegrationProvider.fetch_playback_events`** added to the SDK
  protocol with a default `return []` body so existing Sonarr/Radarr/
  Bazarr/Tdarr providers don't need updates.
- **Plex playback fetcher.** Pulls `/status/sessions/history/all`
  since the last cursor; classifies each entry as direct_play /
  direct_stream / transcode based on the Part's `videoDecision` /
  `audioDecision`; derives reason codes
  (`video.codec.unsupported`, `video.container.unsupported`,
  `audio.codec.unsupported`).
- **Jellyfin playback fetcher.** Snapshots
  `/Sessions?activeWithinSeconds=900`; maps `PlayMethod` →
  decision; translates `TranscodeReasons[]` (`VideoCodecNotSupported`
  → `video.codec.unsupported`). Known limitation: Jellyfin's native
  history is weaker than Plex's — full historical coverage requires
  the Playback Reporting plugin server-side.
- **`PlaybackPoller`** service with savepoint-based dedup so a
  duplicate row doesn't blow away the session. Cursor advance,
  batched MediaFile resolution, drift report.
- **`poll_playback`** ARQ cron tick (every 15 minutes). Per-
  integration error isolation; Sonarr/Radarr/Bazarr/Tdarr filtered
  out.
- **`rule_suggestions` table** + migration 0011. Unique `dedup_key`,
  optional FK to `rules.id` (SET NULL on delete), indexed on
  `status`/`heuristic`/`created_at`.
- **`RuleSuggestionRepository`** with 30-day sticky dismissal
  (`has_recent_dismissal`) and idempotent deploy tracking
  (`has_deployed`).
- **`PlaybackAnalyzer`** service. Runs five heuristics over the last
  30 days of `playback_events` in a single in-memory snapshot:
  `high_transcode_codec`, `bitrate_ceiling`, `container_compat`,
  `resolution_mismatch`, `failed_playback`. Sample-size floor of 20
  events. Per-heuristic thresholds tuned conservatively.
- **`analyze_playback`** ARQ cron tick (daily at 03:00 UTC).
- **API: `GET /rules/suggestions`** — pending suggestions sorted by
  confidence then files_affected.
- **API: `GET /rules/suggestions/{id}`** — full detail including
  evidence JSON.
- **API: `POST /rules/suggestions/{id}/deploy`** — body optionally
  carries `name` / `description` / `priority` / `enabled` /
  `definition` to override the analyzer's draft before saving as a
  Rule. Creates the rule, marks the suggestion `deployed`, sets
  `deployed_rule_id`.
- **API: `POST /rules/suggestions/{id}/dismiss`** — body carries
  `reason`. Transitions to `dismissed`, stamps `dismissed_at` for the
  30-day sticky window.
- **API: `POST /rules/analyze-playback/run`** — admin-only manual
  trigger that bypasses the daily cron.

### Added — frontend

- **Dashboard "Rule suggestions" card.** Lives between the severity
  heatmap and the libraries/integrations row. Renders one row per
  pending suggestion with the heuristic label, suggestion name, the
  3-cell projection (Files affected / Est. runtime / Confidence), and
  Deploy / Review → / Dismiss actions. Shows the first five rows
  inline with a "Show N more" expansion link.
- **"Run now" button** in the card header for admins to re-run the
  analyzer on demand.
- **Empty state with diagnostic copy.** When the analyzer hasn't run
  or skipped due to too few events, the empty state explains why
  (e.g. "Auditarr saw 7 playback events in the last 30 days — needs
  at least 20").
- **Suggestion review modal.** Opens when the user clicks Review →.
  Three tabs: Visual (the Stage 15 `VisualRuleBuilder` pre-populated
  with the suggestion's definition, fully editable before deploy),
  Evidence (structured render of the suggestion's evidence JSON —
  counters in cells, a sample-events table), JSON (raw text editor
  fallback). Editable rule name. Deploy and Dismiss actions in the
  footer.
- **Four new hooks**: `useRuleSuggestions`, `useRuleSuggestion`,
  `useDeploySuggestion`, `useDismissSuggestion`, `useRunAnalyzer`.

### Deferred to a future polish pass

- **Per-integration playback-status row** ("Last polled X ago · N
  events captured · M% transcode rate") on the Integrations page.
  The data is in `playback_events` but exposing it cleanly needs a
  small aggregation endpoint, which didn't fit Turn 3's scope. The
  drift detection above already surfaces the most important
  diagnostic — "your paths don't resolve" — directly on the
  integration's health row.

### Test counts

- Backend: **401/401 pass** (+41 from 360):
  - +16 path-mapping unit tests
  - +5 PlaybackPoller integration tests
  - +9 PlaybackAnalyzer integration tests
  - +11 rule-suggestion API tests
- Frontend: 38/38 pass (existing page-mount smoke covers the new
  Dashboard layout with mocked queries)
- Alembic 0001..0011 round-trip clean

## [1.1.0] — 2026-05-11

Stage 15: visual rule builder. The Rules page's edit dialog gains
Visual / Dry-run / JSON tabs, matching the proposed Auditarr UI
mockup. Stage 14's audit flagged this as the last meaningful visual
gap; this release closes it.

### Added

- **`/api/v1/rules/vocabulary`** new endpoint. Returns the
  authoritative vocabulary the visual builder needs in one call:
  `fields[]` (each with `key`, `label`, `type`, optional `enum`),
  `ops` keyed by field type (`numeric` / `string` / `bool` / `array`),
  the severity scale, and the four action types with their argument
  schemas. Sourced from `app.rules.schema` so the frontend is always
  in lockstep with the backend grammar.
- **Visual rule builder** (`features/rules/VisualRuleBuilder.tsx`).
  Three-column canvas: Trigger → Conditions with WHEN/AND/OR
  conjunction chips → Actions. The combinator (`all` vs `any`) is a
  segmented control on the Conditions column. Each condition row is
  a typed `<select>` for field, operator (filtered to the field's
  type), and value (rendered as text/number/enum/bool/array input
  according to vocabulary). Actions get the same typed-arg treatment
  per the vocabulary's `args_schema`. Nested combinators are
  flattened with a warning banner — JSON mode remains the escape
  hatch.
- **Dry-run tab** (`DryRunPanel`). Pick a media file, post the
  in-edit rule definition to `/rules/dry-run`, render the would-match
  / would-set-severity / would-add-tags / would-queue-optimizations
  outcome inline. No saves; pure preview.
- **JSON tab**. The original textarea editor, preserved as the source
  of truth for complex / nested rules. Visual edits write through to
  JSON; JSON edits parse back into the typed definition on every
  keystroke so the Visual tab stays in sync.
- **`useRuleVocabulary()` hook** with a 1-hour stale time (vocabulary
  rarely changes).

### Changed

- The Rule editor dialog grew from `max-w-3xl` to `max-w-5xl` to
  accommodate the three-column Visual layout, with vertical scroll
  for the metadata fields when the viewport gets short.

### Tests

- +2 backend tests covering the vocabulary endpoint's shape and auth.
- Frontend page-mount smoke still passes — the Visual builder is only
  rendered inside the edit dialog (which the smoke test doesn't open),
  but its module is imported by `RulesPage.tsx` so module-level errors
  would still surface.

### Test counts

- Backend: 360/360 pass (+2 from 358)
- Frontend: 38/38 pass

## [1.0.2] — 2026-05-11

Stage 14.1: visual fidelity pass against the proposed Auditarr UI
mockup. Closes the dashboard + Files-page visual gaps identified in
the Stage 14 audit.

### Added

- **Dashboard sparklines.** `Sparkline` and `SeverityHeatmap` — atomic
  components already in the codebase but never rendered — are now
  wired into `DashboardPage`. Tile labels swapped to the proposed
  wording ("Files audited / Library size / Integrity score / Open
  issues"). The "Open issues" tile shows a delta vs the 7-day average.
  Flat sparkline series are skipped rather than drawn as a horizontal
  line, so tiles without real time-series history stay clean.
- **`/api/v1/dashboard/series?days=30`** new endpoint. Returns four
  daily-rollup arrays (`issues_opened`, `issues_resolved`,
  `integrity_score`, `files_seen`) derived from `ScanRun` and `JobRun`
  history. Bounded `1 ≤ days ≤ 90`.
- **`DashboardOverview.total_size_bytes`** field, computed via
  `SUM(MediaFile.size_bytes)`. Powers the "Library size" tile.
- **Dashboard SeverityHeatmap card** replaces the older thin stacked
  bar. Clicking a segment deep-links into Files filtered by that
  severity (`/files?severity=warn`).
- **Files scope bar** — proposed two-row severity control. Segmented
  "All / Media / Non-media" up top; chip toggles per severity below.
  Chip set is sent to the backend as a comma-separated `severity=`
  query.
- **Backend `/media` accepts comma-separated severities** —
  `MediaRepository.list` now resolves `severity=warn,high,error` into
  an `IN (...)` clause. Single-value callers preserved.
- **Files page deep-link reader** — opens with `?severity=warn` and
  narrows the chip set to just that severity, completing the
  dashboard → files navigation loop.

### Tests

- +4 backend tests covering `total_size_bytes`, `/series` shape,
  `days` bounds, and comma-separated severity filter.

### Test counts

- Backend: 358/358 pass (+4 from 354)
- Frontend: 38/38 pass (no new tests; existing page-mount smoke
  exercises the new dashboard layout via mocked queries)

## [1.0.1] — 2026-05-11

Stage 14: bug hunt + stability + sanity pass after the v1.0 release.

### Security

- **WebSocket auth enforced.** `/api/v1/ws` now requires a valid
  access JWT passed as `?token=...` on the upgrade. Previously any
  unauthenticated process on the same network could subscribe to the
  domain event firehose and read internal state (file paths,
  integration health detail, rule names). Closed with 1008 (policy
  violation) on missing/invalid tokens. Gated by the new
  `ws_require_auth: bool = True` setting; operators on closed
  networks who'd rather not pass tokens through a WS proxy can set
  `AUDITARR_WS_REQUIRE_AUTH=false`.
- **Frontend WS client** appends the current access token from the
  auth store on every connect; token refresh propagates through the
  reconnect loop automatically.

### Bug fixes

- **`Database` singleton stale-settings bug.** `get_database()` cached
  a `Database` instance whose `_settings` was bound at first
  instantiation and never re-read. Tests that `monkeypatch.setenv` a
  different `AUDITARR_DATABASE_URL` and called `get_settings.cache_clear()`
  were silently connecting to the *original* URL — they appeared to
  pass only because the suite default is `sqlite+aiosqlite:///:memory:`
  and every fixture rebuilt the schema in that same in-memory DB.
  Surfaced when Stage 14's WS auth tests switched to a `tmp_path` file
  DB. Fixed by adding an autouse conftest fixture that nulls
  `database._db` between every test, forcing a clean rebuild.
- **`PluginSettingsDialog` state-during-render**. The Stage 12 plugin
  settings dialog seeded its textarea by calling `setText()`
  unconditionally during render, producing a React warning and a dead
  throwaway `useState(() => null)` left in the source with a
  misleading comment. Replaced with a proper `useEffect` + `seeded`
  flag.
- **Dashboard "Optimizations queued" tile** linked to `/automation`
  instead of `/optimization`. Stage 10 added the dedicated Optimization
  page but the dashboard navigation was never updated.
- **TopNav missing the "update available" dot indicator.** Stage 11
  wired it into Sidebar only; users who toggled the alternate top-nav
  layout in Settings never saw new releases.

### Cleanup

- Deleted `frontend/src/components/shell/StagePlaceholder.tsx` — a
  Stage 1 scaffold component defined but never imported by any page.

### Tests

- **Page-mount smoke suite** (`frontend/src/test-pages.test.tsx`):
  12 vitest cases that mount every top-level page in JSDOM with a
  mocked QueryClient + router, verifying each renders without
  throwing and produces visible content. Catches the class of bug
  typecheck and lint can't — `cannot read properties of undefined`,
  stale-hook patterns, import cycles, components that explode on
  loading state.
- **WebSocket auth tests** (`backend/tests/integration/test_ws_auth.py`):
  4 cases covering missing token (1008), invalid token (1008), valid
  token (accept), and the `ws_require_auth=false` opt-out.

### Test counts

- Backend: 354/354 pass (+4 from 350)
- Frontend: 38/38 pass (+12 from 26)
- Alembic 0001..0009 round-trip clean
- Frontend production build: 351 KB JS / 17 KB CSS / 99 KB gzipped

## [1.0.0] — 2026-05-11

The Stage 13 release. Hardening + final installer + v1.0 tag.

### Added
- **End-to-end smoke test** walking the full operator flow
  (auth → library → notification channel → rule → dashboard → plugins
  → updater) so cross-cutting regressions are caught immediately.
- **Security headers middleware** — CSP, X-Content-Type-Options,
  X-Frame-Options, Referrer-Policy, Permissions-Policy on every
  response. HSTS in production only.
- **Auth rate limiting** — sliding-window limiter (`auth_rate_limit_*`
  settings) on `/auth/login`, `/auth/register`, and
  `/auth/password/reset/request`. Default 10 attempts per 5 minutes
  per IP; `0` disables.
- **Housekeeping cron** — daily trim of `notification_deliveries`,
  `update_checks`, `rule_evaluations`, and `job_runs` per
  configurable retention windows. `0` disables that table's trim.
- **One-shot installer** — `install.sh` walks new operators through
  prerequisites, secret-key generation, the first admin user, library
  mounts, and starting the stack. Idempotent.
- **Getting-started docs** — `docs/getting-started/installation.md`
  with the full operator reference.

### Changed
- `Settings.app_version` default is now `1.0.0`.
- Sidebar version label reads `v1.0`.

### Breaking changes
- None. All Stage 13 additions are configurable and default to
  permissive values.

## [0.12.0] — Stage 12: Plugin SDK polish

### Added
- **Lifecycle hooks** — `Plugin.on_startup` and `on_shutdown` alongside
  existing `on_load` / `on_unload`. `on_startup` spawns as a background
  task so host startup never blocks on plugin work.
- **Error isolation** — `_run_lifecycle` catches exceptions, emits
  `plugin.error`, and marks the instance lifecycle-failed so cascading
  hooks skip. A faulty `on_load` no longer aborts the loader run.
- **Plugin settings** — `Plugin.settings_schema` (Pydantic model) gets
  validated, persisted, and exposed in the Settings UI. Migration 0009.
- **Plugin scaffolder** — `auditarr plugin-new <slug>` writes a working
  skeleton (manifest, entry point, README, passing test).
- **Plugin gallery** — operator-configured manifest URL listing
  community plugins, surfaced in Settings → Plugins with an
  `installed` annotation.
- **Authoring guide** — `docs/plugins/authoring.md` covering lifecycle,
  capabilities, settings, testing, publishing.
- 5 new API endpoints under `/api/v1/plugins/*`.

### Changed
- Plugin lifecycle hook ordering is now formally documented: `register`
  → `on_load` → (all plugins loaded) → `on_startup` → … → `on_shutdown`
  → `on_unload`.

## [0.11.0] — Stage 11: Updater

### Added
- Feed client supporting both GitHub Releases and a generic
  `{"version", "changelog"}` shape.
- `UpdateCheck` and `UpdateApply` models (migration 0008).
- `is_newer` version comparator with dev-sentinel + prerelease semantics.
- Sentinel-file apply protocol: container writes the request, host
  helper script runs `docker compose pull && up -d`, container reads
  the status back.
- `docker/updater/auditarr-update.sh` + systemd unit.
- One-click rollback for completed applies.
- 6 endpoints under `/api/v1/updater/*` + `/api/v1/system/version`.
- `update_check_tick` cron.
- Frontend: UpdaterPanel on the Help & updates page, "update available"
  dot on the sidebar.

## [0.10.0] — Stage 10: Optimization system

### Added
- `OptimizationProfile` and extended `OptimizationItem` models
  (migration 0007).
- ffmpeg runner with progress reporting and atomic swap-with-backup.
- Worker state machine: queued → running → completed/skipped/failed.
- `optimization_tick` cron.
- 10 endpoints under `/api/v1/optimization/*`.

## [0.9.0] — Stage 9: Notifications

### Added
- `NotificationChannel` and `NotificationDelivery` models
  (migration 0006).
- 5 providers: email, webhook, Discord, Slack, Apprise.
- Templating + dispatcher pipeline.
- 5 endpoints under `/api/v1/notifications/*`.

## [0.8.0] — Stage 8: Dashboard & analytics

### Added
- `DashboardStats` aggregation service.
- 7 endpoints under `/api/v1/dashboard/*`.
- SidebarBadges hook for live counts.

## [0.7.0] — Stage 7: Automation engine

### Added
- `Schedule`, `JobRun`, `OptimizationItem` models (migration 0005).
- JobCatalogue + cron parser + Scheduler.
- ARQ worker integration.

## [0.6.0] — Stage 6: Rules engine

### Added
- `Rule` and `RuleEvaluation` models (migration 0004).
- Pydantic rule DSL (18 supported fields, severity labels + ranks).

## [0.5.0] — Stage 5: Integrations

### Added
- `Integration` model (migration 0003).
- AES-256-GCM `SecretBox` (wire format v0x01) for credentials.
- 6 connectors: Plex, Jellyfin, Sonarr, Radarr, Bazarr, Tdarr.

## [0.4.0] — Stage 4: Media core

### Added
- `Library`, `MediaFile`, `MediaTag`, `ScanRun` models (migration 0002).
- ffprobe wrapper + media classifier + scanner.

## [0.3.0] — Stage 3: Documentation & help engine

### Added
- markdown-it-py loader.
- Inverted index search.
- Help-key contextual references.

## [0.2.0] — Stage 2: Database & auth

### Added
- argon2id password hashing.
- JWT TokenService (access + refresh).
- Role enum + AuthService.
- First-boot bootstrap admin via env vars.
- `User`, `RefreshSession`, `PasswordResetToken`, `AuditLogEntry`
  models (migration 0001).
- EmailService.

## [0.1.0] — Stage 1: Foundation

### Added
- FastAPI + Pydantic + structlog backend.
- Async SQLAlchemy 2 + Alembic.
- Redis-backed cache.
- Event bus + plugin loader + WebSocketManager.
- Typer CLI.
- Dockerfile + docker-compose.yml + GitHub Actions CI.
- Vite + React 18 + TypeScript strict + Tailwind frontend with shadcn
  components.

---

## Historical appendices

The three appendices below preserve the substance of standalone
documents that previously lived at the project root and under
`docs/migration/`. The originals were process-tracking artefacts
from the pre-audit refactor and audit eras; they're folded in here
so this file is the single historical record. The full original
text is available in older snapshots if archaeology is required.

### Appendix A — Consolidated audit build (was `AUDIT-CONSOLIDATED.md`)

This tree was assembled by applying every fix from the 15-stage
audit-and-fix plan against a baseline. Originally produced as a
single drop-in `auditarr-app/` tree rather than a stack of
incremental zips.

**Frontend — 15 audit stages:**

| Stage | Issue | What changed |
|------:|------:|--------------|
| 1 | 5, 25 | `Button` defaults to `type="button"` so form-internal buttons don't auto-submit |
| 2 | 1, 2, 3 | App shell scroll/overflow fixes (`.app-main`, `.app-main-top`) |
| 3 | 9 | Files sort + codec filter — codec list has `Array.isArray` guard |
| 4 | 6, 20 | Rule operator labels + priority hint |
| 5 | 11 | Sidebar shows the live `app_version` from `/system/version` |
| 6 | 8 | Settings page broken into Workspace / System / Integrations / Security tabs |
| 7 | 7 | Dashboard has Run-scan controls + "Last scanned X ago" |
| 8 | 10 | Enable/Disable controls show explicit "Active"/"Paused" pills |
| 9 | 13 | Integrations editable — secrets blank in edit mode |
| 10 | 15 | Automation merged into Rules page as a tab; `/automation` redirects |
| 11 + expand | 16 | Dashboard sections collapsible (chevron in `CardHead actions`) |
| 12 | 17 | New Changelog page at `/changelog` |
| 13 | 21 | Doc search shows excerpts under each result |
| 14 | 12 | Path mappings panel has explainer copy + tooltips |
| 15 | 23 | `install-bare-metal.sh` fixes for non-Debian Python, Node 18 |

**Backend — one follow-up addition:** the `/system/changelog`
endpoint serves the rendered CHANGELOG to the in-app Changelog
page.

**Items the audit plan explicitly deferred (carried into Phase 2):**

- Issue 18 (docs content rewrite — content work, not code)
- Issue 19 (external link audit — content work, not code)
- Issue 22 (VirusTotal plugin — backend feature, scoped out)
- Issue 24 (notifications query-key reference identity — verified
  non-issue under React Query v5)

### Appendix B — Phase-2 fix completion report (was `FIX-COMPLETION-REPORT.md`)

Single-page summary of the 16-stage audit follow-up. Period:
2026-05-14 → 2026-05-15. Detail lives in the per-stage section at
the top of this file.

**Issues resolved (user-reported):** 1–3, 5, 6, 7, 8, 9, 10, 11,
13, 15, 16, 20, 25 — every in-scope item from the original
`issues.txt`.

**Latent capabilities surfaced (Phase 3 — Stages 12–15):**

| Reference | Stage | Status |
|---|---:|---|
| Playback events read API | 12 | Closed |
| Media tags read API | 13 | Closed |
| Audit log viewer | 14 §A | Closed |
| Per-rule "Matched files" tab | 14b | Closed |
| Housekeeping run-now + last-run | 14 §C | Closed |
| Docs reload button | 14 §D | Closed |
| Per-scan detail | 14 §E | Closed |
| Per-item optimization Run-now | 14 §F | Closed (pre-existing, pinned with tests) |
| Webhook custom method | 15 | Closed |
| Webhook custom headers | 15 | Closed |
| Webhook HMAC body signing | 15 | Closed |

**Latent bugs found and fixed during the audit:**

| Tag | Stage | What |
|---|---:|---|
| L1 | 3 | Codec filter would crash on a non-array response. Fixed with `Array.isArray` guard. |
| L2 | 3 | Toggling every severity off fell through to "no filter" server-side; added `severities_empty=true` sentinel. |
| L3 | 9 | Disposition error path raised `ValueError(disposition)` which Pydantic embedded in `error.ctx`, breaking JSON serialization. Switched to `Literal[...]`. |
| L4 | 12 | `cast(... AS Date)` broke SQLite and produced wrong results on Postgres < 13. Switched to `func.date()`. |
| L5 | 14 | Audit log `ORDER BY occurred_at.desc()` was unstable as a pagination cursor. Switched to `id.desc()`. |
| L6 | 15 | Webhook payload was re-serialized after computing HMAC, signatures wouldn't verify byte-for-byte. Fixed by serializing once and passing `content=body_bytes`. |

**Test counts before and after:**

| Suite | Pre-audit | Post-Stage-16 | Δ |
|---:|---:|---:|---:|
| Backend unit | 322 | 328 | +6 |
| Backend integration | ≈ 405 | 495 | +90 |
| Frontend | ≈ 220 | 316 | +96 |
| **Total** | **≈ 947** | **1,139** | **+192** |

All green. `compileall` clean. `typecheck` clean. `lint` clean.

**Deferred from Phase 2 (still open):** VirusTotal hook integration
UI (plugin-author territory); extension rules settings panel UI
(model + CRUD shipped Stage 9, panel not built); audit log
actor-id autocomplete; live toast on optimization Run now; bulk
tag-sync across integrations.

**Post-deploy verification:** `scripts/post-fix-smoke.sh` hits the
seven endpoints touched by the audit phases. Bring-your-own
admin bearer; exit 0 = all 200.

### Appendix C — Migration ledger (was `docs/migration/*.md`)

Pre-audit refactor era (Stages 0A → 6 in the original numbering).
Captured baselines and tracked the design-system reconstruction
that preceded the audit work. These docs are not user-facing — they
were engineering process artefacts.

**Stage 0A baselines captured:**

| Inventory | Purpose |
|---|---|
| API inventory | Every `frontend/src/hooks/use*.ts` module + `apiClient.ts` — surface map for ADR-004 |
| Bundle baseline | `npm run build` post-CSS-fix; 503.72 kB JS / 144.19 kB gzip with one expected chunk-size warning |
| Component inventory | Shared `components/ui/*` + `components/shell/*` plus duplicate-pattern scan across features |
| Design-token inventory | `styles/tokens.css` vs the upstream design package |
| Route inventory | `app/AppRoutes.tsx` + `components/shell/nav.ts` route table |
| Test baseline | Pre-refactor vitest run snapshot |

**Stage 0A.0 CSS fix:** import-order corrected so the production
build emits zero CSS warnings.

**Stages 1–6 highlights:**

- **Stage 1:** 12 new UI primitives in `components/ui/` (Page,
  Input, Select, Textarea, Switch, Modal, Drawer, Tabs, Toolbar,
  FilterBar, DataGrid, Metric, Segmented). ADR-005 enforced
  (`apiClient` singleton, no dynamic imports). ADR-006 enforced
  (three `--border` token values reconciled). Nine layout tokens
  promoted, animation keyframes added.
- **Stage 2 (Runtime Settings Canonicalization):** every settings
  surface routed through the runtime settings panel; JSON-only
  configuration paths removed where structured forms existed.
- **Stages 3–6:** page-scaffold conversion (Files, Rules,
  Optimization, Integrations, Notifications, Settings). Tested at
  309/309 backend unit + 19/19 runtime-settings integration +
  166/166 frontend at the snapshot point.
- **Stage 5b/6b primitive-adoption cleanup:** consolidated
  duplicate patterns surfaced by the Stage 0A component inventory.

**Items that remained blocked at the migration ledger's last
commit:** Playwright visual regression baseline (would have
unblocked Stages 3b/4b/6b DOM-pinning adoption). The audit-era
work proceeded without it because the regression bar shifted to
behavioural tests at that point.

**Stage order at the time of the migration ledger's final entry:**
Stage 7 (Dashboard) was queued next, with Stage 8
(Stability/Security/Performance) as the closer including bundle-size
discipline. The audit-and-fix plan superseded the original stage
order from Stage 7 forward.

---

End of historical appendices.
