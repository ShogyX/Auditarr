# Auditarr

Self-hosted media library auditor. Modular monolith, plugin-driven, Docker-first.

> **Stage 1 — Foundation.** The application boots, the plugin system loads, the
> frontend shell renders, the Docker stack runs, and CI passes. Concrete features
> land in subsequent stages per the project specification.

## Quick start

### Docker (recommended for most installs)

```bash
cp .env.example .env
# Edit .env and set AT MINIMUM:
#   AUDITARR_SECRET_KEY=<run: python -c "import secrets; print(secrets.token_urlsafe(64))">
#   POSTGRES_PASSWORD=<a strong password>
#   AUDITARR_BOOTSTRAP_ADMIN_USERNAME=admin
#   AUDITARR_BOOTSTRAP_ADMIN_PASSWORD=<at least 12 characters>
#   AUDITARR_BOOTSTRAP_ADMIN_EMAIL=admin@example.com

docker compose up -d
open http://localhost:8000              # API + UI
```

If you forget the bootstrap admin variables on first boot, the database starts
empty and nobody can log in. The application logs a `WARNING` with the exact
remediation. Set the variables and `docker compose restart app`.

### Bare-metal (LXC / VM, no Docker)

For Proxmox LXC containers, small VMs, or hosts where Docker isn't an
option, run the bare-metal installer. Tested on Debian 12 / Ubuntu
22.04 / Ubuntu 24.04.

```bash
sudo ./install-bare-metal.sh
```

This installs Python 3.12, PostgreSQL, Redis, ffmpeg, and nginx (the
nginx step is optional), creates an `auditarr` system user, lays down
the application under `/opt/auditarr`, generates
`/etc/auditarr/auditarr.env`, runs migrations, prompts for the first
admin user, and installs `auditarr-api.service` +
`auditarr-worker.service` systemd units. The script is idempotent —
re-running it on an existing install preserves the secret key and admin
user, refreshes the application files, and restarts the services.

See [docs/getting-started/install-bare-metal.md](docs/getting-started/install-bare-metal.md)
for the full operator guide (non-interactive mode, env-var knobs,
update path, uninstall, troubleshooting).

### Local development

Prerequisites: **Python 3.12+**, **Node 22+**, **uv** (`pipx install uv`),
**PostgreSQL 16**, **Redis 7**.

```bash
make bootstrap      # install backend + frontend deps
cp backend/.env.example backend/.env
make migrate        # run alembic upgrade head
make dev            # backend on :8000, frontend on :5173 (vite proxies /api)
```

Frontend dev server is at `http://localhost:5173`.
Backend API at `http://localhost:8000/api/v1/`. The OpenAPI / Swagger UI is
at `http://localhost:8000/api/v1/swagger`. The in-app documentation engine
serves user-facing documentation under `/api/v1/docs/`.

## Repository layout

```
auditarr/
├── backend/                 FastAPI app, SQLAlchemy 2 async, Alembic, plugin loader
│   ├── app/
│   │   ├── api/             v1 routers, middleware, error handlers, websocket
│   │   ├── core/            settings, logging, registry, exceptions
│   │   ├── events/          domain event bus
│   │   ├── plugins/         manifest schema, contracts, loader
│   │   ├── storage/         async DB engine, Redis client
│   │   └── main.py / cli.py app factory + ops CLI
│   ├── migrations/          alembic
│   ├── plugins/             on-disk plugins (example-hello included)
│   └── tests/               pytest unit + integration
├── frontend/                Vite + React 18 + TS strict + Tailwind
│   └── src/
│       ├── app/             providers + routes
│       ├── components/      ui atoms + shell (sidebar, topnav, header)
│       ├── features/        per-page feature modules
│       ├── hooks/  lib/  services/  stores/  plugins/  types/  styles/
├── docker/                  entrypoint + docker-side update watcher
├── updater/                 bare-metal update watcher (Stage 19)
├── scripts/                 healthcheck
├── docker-compose.yml       app + postgres + redis
├── Dockerfile               multi-stage (frontend build → uv backend → runtime)
├── install.sh               docker installer (recommended path)
├── install-bare-metal.sh    LXC / VM installer (systemd + native postgres + redis)
└── Makefile                 day-to-day developer commands
```

## Architecture in one paragraph

A modular monolith with hard contracts: every cross-module call goes through the
**event bus**, **service registry**, or the typed **plugin SDK** — never through
direct imports. Plugins live in their own directories with a `manifest.json` and
a backend entrypoint defining `register(context)`. They cannot touch database
sessions, repositories, or the frontend shell. The API is versioned at
`/api/v1/`; breaking changes require `/api/v2/`. All state changes emit normalized
domain events (`media.added`, `scan.completed`, etc.) that flow to subscribers,
the websocket bridge, the audit log, and the dashboard.

## Useful commands

```bash
make help             # list everything
make backend          # run only the API
make frontend         # run only the SPA
make lint typecheck test
make docker-up docker-down docker-logs
make rev MSG="add users table"
make migrate
```

The backend ships its own CLI:

```bash
cd backend && uv run auditarr --help
uv run auditarr db-check
uv run auditarr redis-check
uv run auditarr plugin-list
```

## Plugin system

Drop a directory into `backend/plugins/`:

```
backend/plugins/my-plugin/
├── manifest.json
└── backend.py
```

`manifest.json`:

```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "version": "0.1.0",
  "type": "integration",
  "backend_entry": "backend.py",
  "routes": true,
  "capabilities": ["my.capability"]
}
```

`backend.py`:

```python
from app.plugins import Plugin, PluginContext

def register(context: PluginContext) -> Plugin:
    @context.router.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "true"}
    return Plugin(context)
```

The loader auto-discovers it on startup. See `backend/plugins/example-hello/`
for the canonical reference.

## Stage roadmap

All thirteen stages complete + Stage 14 stability pass + Stage 14.1
dashboard polish + Stage 15 visual rule builder + Stage 16 data-driven
rule recommendations + Stage 17 stability pass on Stage 16 + Stage 18
bare-metal installer for LXC / VM + Stage 19 install-mode-aware
updater + Stage 20 UI polish + Settings expansion + Stage 21
runtime-editable settings backend. Auditarr is **v1.6.0**.

| Stage | Scope                                       | Status |
|-------|---------------------------------------------|--------|
| 1     | Foundation                                  | ✅ done |
| 2     | Database & auth                             | ✅ done |
| 3     | Documentation & help engine                 | ✅ done |
| 4     | Media core (scanner, ffprobe)               | ✅ done |
| 5     | Integrations                                | ✅ done |
| 6     | Rules engine                                | ✅ done |
| 7     | Automation engine                           | ✅ done |
| 8     | Dashboard & analytics                       | ✅ done |
| 9     | Notifications                               | ✅ done |
| 10    | Optimization system                         | ✅ done |
| 11    | Updater                                     | ✅ done |
| 12    | Plugin SDK polish                           | ✅ done |
| 13    | Hardening & QA + final installer            | ✅ done |
| 14    | Bug hunt + stability + sanity               | ✅ done |
| 14.1  | Dashboard polish + Files scope              | ✅ done |
| 15    | Visual rule builder                         | ✅ done |
| 16    | Data-driven rule recommendations            | ✅ done |
| 17    | Stage 16 stability + parser hardening       | ✅ done |
| 18    | Bare-metal installer (LXC / VM)             | ✅ done |
| 19    | Install-mode-aware updater                  | ✅ done |
| 20    | UI polish + Settings expansion              | ✅ done |
| 21    | **Runtime-editable settings backend**       | ✅ done |

See [CHANGELOG.md](CHANGELOG.md) for the per-stage changes.

## Stage 16 data-driven rule recommendations

Auditarr now polls Plex and Jellyfin for playback telemetry every 15
minutes, analyzes the last 30 days of events daily, and surfaces
recurring problem patterns (transcodes, bitrate ceilings, container
compatibility issues, failed playbacks) as rule suggestions on the
Dashboard. One click deploys the suggested rule; "Review →" opens the
Stage 15 visual builder pre-populated with the analyzer's draft plus
an Evidence tab showing the actual playback events behind the
recommendation. Dismissed suggestions stay quiet for 30 days.

Path remapping is configurable per-integration so Plex's `/data/...`
view and Auditarr's `/mnt/media/...` view reconcile cleanly; if most
playback paths fail to resolve, the integration is flagged degraded
with a prompt to configure mappings.

## Updater

Auditarr checks a configurable release feed (defaults to the project's
GitHub Releases) and surfaces "update available" on the sidebar and the
**Help & updates** page. Applying an update writes a sentinel file the
host-side helper script picks up — that script lives in
`docker/updater/` and does the actual `docker compose pull && up -d`.
Every check and every apply is persisted to the audit log; rollback is
a single click.

See `docs/updater/overview.md` (Help drawer → Updates) for the feed
shape, sentinel protocol, helper installation, and rollback semantics.

## Optimization

The pipeline that started with rules queueing `queue_optimization`
actions now executes them. Operators define **profiles** (codec,
container, audio handling, scale, container choice) under
**Optimization**; the worker picks the oldest queued item every
minute and runs ffmpeg with progress streamed back to the UI. Output is
validated by ffprobe and atomically swapped into place with an optional
`.bak` of the original.

Start the worker with `docker compose --profile worker up -d`. Without
it, items sit queued until you click **Run next**.

See `docs/optimization/overview.md` (Help drawer → Optimization) for
the profile schema, supported codecs, queue state transitions, and the
full endpoint reference.

## Notifications

Rule `notify` actions are now wired up. Operators configure channels
(email, webhook, Discord, Slack, Apprise, plus anything plugins
register) under **Notifications** with per-channel severity thresholds.
Every send attempt — including channels filtered out by the threshold —
is recorded in a delivery log. Channels can be tested on demand without
firing a rule.

See `docs/notifications/overview.md` (Help drawer → Notifications) for
the channel kinds, templating variables, threshold semantics, and
endpoint reference.

## Dashboard

The home page now shows real numbers. Overview metrics (files, issues
open, rules enabled, optimizations queued), severity histogram across
the whole library and per library, integration health grid, top rules
by match count, and recent scan/automation activity — all driven by
SQL aggregations over existing tables (no new schema in Stage 8). The
sidebar badges next to Files / Rules / Optimization are also live.

See `docs/dashboard/overview.md` (Help drawer → Dashboard) for the
endpoint reference and how the numbers are computed.

## Automation

The Automation page is where you wire repeated work to a cadence. The
built-in jobs are `scan_library`, `evaluate_library`,
`healthcheck_integration`, and `sync_integration_tags`. Schedules are
stored in the DB and ticked every minute by the ARQ worker; run with
`docker compose --profile worker up -d` to get out-of-process execution.

A typical first set of schedules:

```
Nightly scan          job=scan_library          cron={hour:3, minute:0}
Hourly Sonarr sync    job=sync_integration_tags cron={minute:7}
Hourly rule eval      job=evaluate_library      cron={minute:30}
```

Every job run is logged in `job_runs` with status + duration + result;
the Automation page surfaces the last 20.

## Rules

Rules are JSON documents matched against every media file. They set
severity, add tags, queue optimizations, or send notifications. Rules
re-evaluate automatically after every scan, or manually from the **Rules**
page (pick a library, click **Evaluate**).

A minimal rule that flags fat HEVC files:

```json
{
  "match": {
    "all": [
      { "field": "video_codec", "op": "eq", "value": "hevc" },
      { "field": "bitrate_kbps", "op": "gt", "value": 25000 }
    ]
  },
  "actions": [
    { "type": "set_severity", "severity": "warn" },
    { "type": "add_tag", "tag": "fat-hevc" }
  ]
}
```

See `docs/rules/reference.md` (Help drawer → Rule reference) for the
full DSL: supported fields, operators, actions, severity scale, and
how integration-mirrored tags (Sonarr/Radarr/Bazarr) compose with rule
conditions.

## Connecting an integration

1. Open **Settings → Integrations** (or POST `/api/v1/integrations`).
2. Pick a connector from the available list (Plex, Jellyfin, Sonarr,
   Radarr, Bazarr, Tdarr).
3. Fill in the server URL and API key. Secrets are encrypted at rest
   using AES-256-GCM with a key derived from `AUDITARR_SECRET_KEY`.
4. Click **Connect** and run a healthcheck.
5. Expand the row and **Discover** libraries — for upstream services
   that own libraries (Plex, Jellyfin, Sonarr, Radarr, Tdarr), each
   discovered library can be one-click promoted to an Auditarr-managed
   library.

For background healthchecks, run the worker profile:

```bash
docker compose --profile worker up -d
```

The worker polls every enabled integration on its `poll_interval_seconds`
cadence and emits an `integration.health_changed` event whenever the
status changes.

## Scanning your library

1. **Add a library** in Settings → Libraries (or `POST /api/v1/libraries`).
   Set `name`, `root_path` (a path inside the container), and `kind`.
2. **Trigger a scan**: click *Run scan* on the Files page after picking a
   library, or `POST /api/v1/scans/libraries/{id}` (requires admin).
3. Auditarr walks the directory, runs ffprobe on every media candidate, and
   inserts/updates `MediaFile` rows. Files that vanish between scans get
   flagged `is_orphaned=true`.

For long scans, opt into the background worker:

```bash
docker compose --profile worker up -d
# Then trigger with the enqueue flag — the API returns immediately.
curl -X POST 'http://localhost:8000/api/v1/scans/libraries/{id}?enqueue=true' \
     -H 'authorization: Bearer <admin token>' \
     -H 'content-type: application/json' \
     -d '{"mode":"full"}'
```

## Documentation

Auditarr ships its own documentation engine. Markdown files under `docs/`
are loaded at startup, frontmatter is parsed, and the content is exposed
both through the in-app **Help** page and as contextual help drawers on
every screen (keyboard shortcut: ⌘/ or Ctrl+/).

Add documentation by dropping a Markdown file under `docs/` and either
restarting the service or — as an admin — POSTing to
`/api/v1/docs/reload`.

Frontmatter schema:

```yaml
---
id: rules/conditions          # optional; falls back to path
title: Rule conditions
category: rules               # groups pages in the sidebar
tags: [rules, syntax]
summary: One-sentence description.
help_context: [rules.conditions]   # which UI screens this helps
related: [rules/actions]
---
```

Pages with `help_context: [foo.bar]` will appear in the contextual help
drawer on any screen that calls `useHelpKey("foo.bar")`.

## First-boot admin

To bootstrap an initial admin account on a fresh install, set these env vars
before the first `docker compose up`:

```bash
AUDITARR_BOOTSTRAP_ADMIN_USERNAME=admin
AUDITARR_BOOTSTRAP_ADMIN_PASSWORD=at-least-twelve-characters
AUDITARR_BOOTSTRAP_ADMIN_EMAIL=admin@example.com
```

The lifespan only creates the user if **no users exist**, so the variables can
remain in place after the first boot without re-creating it.

## License

MIT.
