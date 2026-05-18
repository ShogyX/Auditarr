<p align="center">
  <img src="docs/assets/logo.jpg" alt="Auditarr logo — bewildered grandma squinting at a laptop" width="480">
</p>

<h1 align="center">Auditarr</h1>

<p align="center">
  <strong>NOTE! This is AI slop, dont trust it with anything you consider sensitive. Restricted network/internet access is highly recommended!</strong>
  
  <strong>The self-hosted audit layer for your media library.</strong>
</p>

<p align="center">
  Scans your files, runs them through rules you define, and surfaces what needs attention —
  fat HEVC, missing subtitles, transcode-heavy titles, orphan files —
  so you can fix it once instead of finding it again next month.
</p>

<p align="center">
  <a href="#installation"><img src="https://img.shields.io/badge/install-docker%20%7C%20bare--metal-blue?style=flat-square" alt="Install"></a>
  <a href="#license"><img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License: MIT"></a>
  <a href="#tech-stack"><img src="https://img.shields.io/badge/python-3.12+-blue?style=flat-square" alt="Python 3.12+"></a>
  <a href="#tech-stack"><img src="https://img.shields.io/badge/node-22+-brightgreen?style=flat-square" alt="Node 22+"></a>
  <a href="#tech-stack"><img src="https://img.shields.io/badge/postgres-16-blue?style=flat-square" alt="Postgres 16"></a>
</p>

<p align="center">
  <a href="#what-it-does">What it does</a> ·
  <a href="#installation">Installation</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#integrations">Integrations</a> ·
  <a href="#documentation">Documentation</a> ·
  <a href="#contributing">Contributing</a>
</p>

---

## What it does

You run Plex or Jellyfin. You let Sonarr and Radarr download things. After a while, your library has thousands of files, and you have no idea which ones are quietly causing transcode pressure on a Friday night, or which Sonarr renames left behind orphaned files, or whether that one 4K remux is missing the English subtitles your kids actually need.

Auditarr is the layer that answers those questions automatically. It:

- **Scans** every file in your library and extracts technical metadata via `ffprobe`.
- **Evaluates** each file against rules you write (or rules it suggests based on real playback data from Plex/Jellyfin).
- **Surfaces** the problems on a dashboard — severity rollups, codec mix, integration health, top-matching rules.
- **Acts** on findings via automations: queue an optimization, send a Slack message, fire a webhook, mark something for review.
- **Talks** to the rest of your stack (Plex, Jellyfin, Sonarr, Radarr, Bazarr, Tdarr) over official APIs and reacts in seconds to webhook events from those services.

It is **not** a media manager. It doesn't download anything, it doesn't rename anything, and it doesn't touch your files unless you explicitly opt into the optimization pipeline (which transcodes files you've flagged into something smaller or more compatible).

It **is** an audit-and-decision layer that sits next to your existing stack and tells you what to fix.

---

## Installation

Two ways in: Docker (`install-docker.sh`) for most operators, or bare-metal (`install-bare-metal.sh`) for LXC / VMs without Docker.

### Docker (recommended)

The installer walks you through generating a secret key, creating the first admin, and bind-mounting your libraries.

```bash
git clone https://github.com/YOUR-ORG/auditarr.git
cd auditarr
./install-docker.sh
```

If you'd rather drive Docker yourself:

```bash
cp .env.example .env
# Set AT MINIMUM:
#   AUDITARR_SECRET_KEY=<run: python -c "import secrets; print(secrets.token_urlsafe(64))">
#   POSTGRES_PASSWORD=<a strong password>
#   AUDITARR_BOOTSTRAP_ADMIN_USERNAME=admin
#   AUDITARR_BOOTSTRAP_ADMIN_PASSWORD=<at least 12 characters>
#   AUDITARR_BOOTSTRAP_ADMIN_EMAIL=admin@example.com

docker compose up -d
```

Open `http://localhost:8000` and log in with the admin account you bootstrapped.

For background scans, automations, and the optimization worker, also start the worker profile:

```bash
docker compose --profile worker up -d
```

**Library access.** Auditarr can only audit files it can read. Bind-mount your library paths into the `app` service in `docker-compose.override.yml` (the installer generates this for you when you supply library paths during the prompts). See [`docs/getting-started/installation.md`](docs/getting-started/installation.md) for the full pattern.

### Bare-metal (LXC, VM, anywhere Docker isn't)

Tested on Debian 12, Ubuntu 22.04, Ubuntu 24.04. Installs Python 3.12, PostgreSQL 16, Redis 7, ffmpeg, and (optionally) nginx; creates a service user, lays the app under `/opt/auditarr`, runs migrations, registers systemd units.

```bash
sudo ./install-bare-metal.sh           # interactive
sudo ./install-bare-metal.sh --auto    # non-interactive, auto-generates admin
sudo ./install-bare-metal.sh -y        # same as --auto
sudo ./install-bare-metal.sh --help    # full flag and env-var reference
```

The interactive flow asks (in order) email, username, password — with password confirmation and a 12-char minimum.

In non-interactive mode (`--auto`), credentials and paths come from `AUDITARR_*` environment variables. The full set lives in the comment block at the top of `install-bare-metal.sh` — that script is the canonical reference for the variable names and defaults, so it can't drift out of sync with this README. Common ones:

- `AUDITARR_ADMIN_EMAIL`, `AUDITARR_ADMIN_USERNAME`, `AUDITARR_ADMIN_PASSWORD`
- `AUDITARR_HOME` (defaults to `/opt/auditarr`)
- `AUDITARR_INSTALL_NGINX` (`yes` | `no` | `prompt`)
- `AUDITARR_NONINTERACTIVE=1` (forces non-interactive mode without `--auto`)

The script is idempotent. Re-running preserves your secret key and admin user, refreshes application files, and restarts services.

Full operator guide: [`docs/getting-started/install-bare-metal.md`](docs/getting-started/install-bare-metal.md).

### Local development

Prerequisites: Python 3.12+, Node 22+, [uv](https://docs.astral.sh/uv/) (`pipx install uv`), PostgreSQL 16, Redis 7.

```bash
make bootstrap                # install backend + frontend deps
cp backend/.env.example backend/.env
make migrate                  # alembic upgrade head
make dev                      # backend :8000, frontend :5173 (proxied)
```

API: `http://localhost:8000/api/v1/`. OpenAPI/Swagger UI: `http://localhost:8000/api/v1/swagger`. Frontend: `http://localhost:5173`.

---

## How it works

A typical run, top to bottom:

1. **Scan.** Auditarr walks each library's root path and runs `ffprobe` on every media file. Updates are incremental — only changed/new files are re-probed.
2. **Classify.** Codec, bitrate, dimensions, container, audio and subtitle languages, framerate — all extracted and stored against the file row.
3. **Evaluate.** The rules engine runs every enabled rule against each file. Rules are JSON; you can write them by hand or use the visual builder.
4. **Act.** Matched rules can set severity, add tags, queue optimizations, send notifications, or fire webhooks.
5. **React.** Webhooks from Sonarr / Radarr / Plex / Jellyfin land directly on Auditarr and trigger per-file reprocessing — no waiting for the next poll cycle.

### A rule, end to end

```json
{
  "name": "Fat HEVC files",
  "match": {
    "all": [
      { "field": "video_codec",  "op": "eq", "value": "hevc" },
      { "field": "bitrate_kbps", "op": "gt", "value": 25000 }
    ]
  },
  "actions": [
    { "type": "set_severity", "severity": "warn" },
    { "type": "add_tag",      "tag": "fat-hevc" },
    { "type": "notify",       "channels": ["slack-ops"] }
  ]
}
```

That's all. The rule runs after every scan, after any file change webhook from your *arr stack, or on demand via "Evaluate library" on the Rules page.

Rules can also key off **integration-synced tags** — anything Sonarr / Radarr / Bazarr puts on a series or movie shows up as a file tag and is matchable like any other field. Pair that with the **tag scope** on automations and you can say "re-evaluate only files tagged `4k-remux`" instead of walking the whole library.

### Rule recommendations from real playback

If you connect Plex or Jellyfin, Auditarr polls each one for playback events (which file, what decision, why — direct stream, direct play, transcode), analyzes the last 30 days nightly, and surfaces patterns as suggested rules on the dashboard. "Your audience transcoded these 12 titles 47 times this week. Here's a draft rule that would flag the underlying codec issue." One click deploys it; "Review" opens the visual builder pre-populated with the analyzer's draft plus an evidence tab showing the actual playback events.

### Webhook ingress (push from the *arr stack)

Auditarr ships receiver endpoints at `POST /api/v1/webhooks/{kind}/{integration_id}` for Sonarr, Radarr, Plex, and Jellyfin. Configure each upstream to hit Auditarr with a HMAC-SHA256-signed body. Add / rename → reprobe + content hash. Delete → mark orphaned. Test events return 200 OK with no work done. Unknown event types are quietly ignored (no retry storm).

A per-integration webhook secret is generated via a one-shot admin endpoint — Auditarr stores only the ciphertext, displays the plaintext exactly once.

### Optimization

If a rule says "queue this for optimization," it lands in the queue with the chosen profile (codec, container, audio handling, scale). The optimization worker picks the oldest queued item every minute, runs ffmpeg, streams progress back to the UI, ffprobes the output, and atomically swaps it in with an optional `.bak` of the original.

```bash
docker compose --profile worker up -d   # enable the worker
```

Without the worker profile, queued items wait until you click **Run next**.

### Content hashing + VirusTotal

When a webhook fires (or you manually trigger a reprobe), Auditarr computes the file's SHA-256 once and caches it. If you've configured a VirusTotal API key, it looks up the hash on VT's free-tier endpoint — no content is uploaded, just the hash. The file drawer shows you a clean / suspicious / malicious / unknown pill plus a click-through to the full report.

---

## Integrations

Out of the box:

| Service   | Purpose | What Auditarr does with it |
|-----------|---------|----------------------------|
| **Plex**     | Media server     | Playback telemetry, library discovery, webhook ingress (`library.new`) |
| **Jellyfin** | Media server     | Playback telemetry, library discovery, webhook ingress (`ItemAdded`/`Updated`/`Removed`) |
| **Sonarr**   | TV manager       | Tag sync, library discovery, webhook ingress (`Download`/`Rename`/`EpisodeFileDelete`) |
| **Radarr**   | Movie manager    | Tag sync, library discovery, webhook ingress (`Download`/`Rename`/`MovieFileDelete`) |
| **Bazarr**   | Subtitles        | Tag sync (subtitle presence per language) |
| **Tdarr**    | Transcoding farm | Library discovery, status sync |

Add your own via the plugin system — see [Plugin development](#plugin-development) below.

---

## Tech stack

**Backend** — Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, asyncpg, Pydantic 2, structlog, ARQ workers, ffprobe, AES-256-GCM for at-rest secret encryption.

**Frontend** — Vite, React 18, TypeScript (strict), Tailwind, TanStack Query, Radix UI primitives.

**Storage** — PostgreSQL 16 for persistent state, Redis 7 for the job queue + cache + pubsub.

**Architecture** — modular monolith with hard contracts. Every cross-module call goes through one of: the **event bus**, the **service registry**, or the typed **plugin SDK**. Direct cross-module imports are not allowed by design; the test suite enforces it.

---

## Plugin development

Drop a directory into `backend/plugins/`:

```
backend/plugins/my-plugin/
├── manifest.json
└── backend.py
```

**`manifest.json`**

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

**`backend.py`**

```python
from app.plugins import Plugin, PluginContext

def register(context: PluginContext) -> Plugin:
    @context.router.get("/ping")
    async def ping() -> dict[str, str]:
        return {"ok": "true"}
    return Plugin(context)
```

The loader auto-discovers it on startup. The plugin can register integration providers, notification channels, rule action types, automation jobs, or pure HTTP routes — see `backend/plugins/example-hello/` for the canonical reference and [`docs/plugins/`](docs/plugins/) for the contracts.

Plugins **cannot** touch the database directly, repositories, the event bus internals, or the frontend shell. The SDK is the only contact surface.

---

## API

Versioned at `/api/v1/`. OpenAPI spec at `http://your-host:8000/api/v1/openapi.json`, Swagger UI at `/api/v1/swagger`.

A few of the more useful endpoints:

```
GET   /api/v1/media                              # list files with filters
POST  /api/v1/scans/libraries/{id}               # trigger a scan
GET   /api/v1/rules                              # list rules
POST  /api/v1/rules/{id}/dry-run                 # see what a rule would match
POST  /api/v1/integrations                       # add an integration
POST  /api/v1/integrations/{id}/webhook-secret   # rotate webhook secret
POST  /api/v1/webhooks/{kind}/{integration_id}   # receive an upstream webhook
GET   /api/v1/tags                               # tag catalog (rules + automations use this)
GET   /api/v1/playback/stats/transcoded          # top-transcoded files (last 30d)
GET   /api/v1/dashboard                          # overview metrics
```

Everything else is in the OpenAPI spec.

---

## Documentation

Auditarr ships its own documentation engine. Markdown files under [`docs/`](docs/) are parsed at startup and surfaced two ways:

1. The in-app **Help** page lists every doc by category.
2. Every screen has a contextual help drawer (⌘/ or Ctrl+/) that shows pages tagged with the matching `help_context`.

Highlights:

- [`docs/getting-started/`](docs/getting-started/) — installation, first scan, troubleshooting
- [`docs/integrations/`](docs/integrations/) — per-service setup walkthroughs (Plex, Jellyfin, Sonarr, Radarr, Bazarr, Tdarr) and the webhook ingress guide
- [`docs/rules/`](docs/rules/) — full rule DSL: fields, operators, actions, severity, integration-tag composition
- [`docs/automation/`](docs/automation/) — built-in jobs, cron syntax, tag scope
- [`docs/optimization/`](docs/optimization/) — profile schema, supported codecs, queue state transitions
- [`docs/notifications/`](docs/notifications/) — channel kinds (email, webhook, Discord, Slack, Apprise), template variables, thresholds
- [`docs/plugins/`](docs/plugins/) — plugin SDK contracts

Add your own docs by dropping a Markdown file under `docs/` and POSTing to `/api/v1/docs/reload` (admin only) — no service restart needed.

---

## Operational notes

### Behind a reverse proxy

Nothing about Auditarr is unusual to reverse-proxy. Forward `Host`, `X-Forwarded-For`, `X-Forwarded-Proto`. WebSocket upgrade required on `/api/v1/ws`.

### Behind a security gateway (UniFi, OPNsense, pfSense IDS/IPS)

Auditarr is a REST application. It uses the standard HTTP `DELETE` method for deleting resources — there are 11 backend endpoints and 12 frontend call sites that issue DELETE requests. Some IDS/IPS rulesets ship with legacy signatures that flag *any* HTTP DELETE as suspicious (e.g. the Snort GPL `WEB_SERVER DELETE attempt` signature included in UniFi's default ruleset).

These signatures predate REST APIs by roughly 20 years. The right fix is to **suppress the specific signature** in your security gateway rather than work around it in the application. UniFi: *Settings → Security → Intrusion Prevention → Signature Suppression*.

### Backups

Back up `/var/lib/auditarr` (bare-metal) or the named volumes (Docker) plus your Postgres database. The secret key in `/etc/auditarr/auditarr.env` decrypts all stored integration secrets — back it up separately and treat it like a private key.

### Logs

Structured JSON via `structlog`. `journalctl -u auditarr-api.service` on bare-metal; `docker compose logs -f app` for Docker. The audit log inside the app surfaces every config change, scan, rule evaluation, and notification delivery — searchable from the UI.

---

## Repository layout

```
auditarr/
├── backend/                       FastAPI app, SQLAlchemy 2 async, Alembic
│   ├── app/
│   │   ├── api/v1/                routers (media, rules, integrations, webhooks, …)
│   │   ├── automation/            scheduler + job runners
│   │   ├── services/              scanner, rules, virustotal, file_hash, webhooks
│   │   ├── models/                SQLAlchemy models
│   │   └── …
│   ├── migrations/versions/       Alembic migrations
│   ├── plugins/                   built-in + example plugins
│   └── tests/                     pytest unit + integration
├── frontend/                      Vite + React 18 + TS strict + Tailwind
│   └── src/
│       ├── features/              per-page feature modules
│       ├── hooks/                 per-domain React Query hooks
│       ├── components/ui/         Modal, Drawer, Button, primitives
│       └── …
├── docs/                          user-facing docs (rendered in-app + on GitHub)
├── docker/                        Dockerfile, entrypoint, updater watcher
├── docker-compose.yml             app + postgres + redis (+ optional worker)
├── install-docker.sh              Docker installer (was install.sh in v1.6)
├── install-bare-metal.sh          LXC / VM installer
└── Makefile                       day-to-day developer commands
```

---

## Contributing

Issues and pull requests welcome. A few ground rules:

- **Tests are required** for behavioral changes. Backend tests under `backend/tests/{unit,integration}/`, frontend tests alongside the file under test as `*.test.tsx`.
- **Migrations** must run on both SQLite (used by the test suite) and PostgreSQL (production). Prefer SQL-standard syntax; avoid Postgres-specific extensions like `E'...'` escape strings or `varchar` length overflows. Revision IDs are capped at 32 characters (Alembic default).
- **No cross-module imports.** Use the event bus, the service registry, or the plugin SDK.
- **Lint + typecheck + tests must pass** before review. `make lint typecheck test` is the canonical command.

Found a bug? Open an issue with steps to reproduce, the log line, your install mode (Docker / bare-metal / dev), and the version (`auditarr --version` or check the footer of the dashboard).

---

## License

MIT. See [`LICENSE`](LICENSE).

---

<p align="center">
  <sub>Auditarr is not affiliated with Plex, Jellyfin, Sonarr, Radarr, Bazarr, Tdarr, or VirusTotal. All trademarks belong to their respective owners.</sub>
</p>
