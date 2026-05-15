---
id: getting-started/installation
title: Installation
category: getting-started
tags: [install, docker, setup, getting-started]
summary: Get Auditarr running on a Docker host in five minutes.
help_context: [getting-started.install]
related: [updater/overview, plugins/authoring]
---

# Installation

Auditarr ships as a Docker image (covered here) **and** as a
bare-metal installer for LXC containers and VMs. Pick the path that
matches your environment:

- **Docker** — recommended for most installs. The rest of this page.
- **Bare-metal (LXC / VM)** — for hosts where Docker isn't available
  or desired. See [getting-started/install-bare-metal](install-bare-metal).

The two paths use the same backend, worker, and frontend — they
differ only in how the runtime is packaged and supervised.

## Docker install

The recommended Docker path uses `install.sh`, which walks you
through prerequisites, secrets, the first admin user, library
mounts, and starting the stack.

If you'd rather configure everything by hand, the **Manual setup**
section below describes what the installer is doing under the hood.

## Requirements

- Linux host with **Docker Engine ≥ 24** and the **`docker compose` v2
  plugin**. Docker Desktop on macOS or Windows also works for kicking
  the tires; for real deployments use a Linux host with persistent
  storage.
- 1 GB RAM free (Postgres + Redis + the app together use ~600 MB at
  rest, with headroom for scans).
- `openssl` and `python3` available on the host (the installer uses
  them for key generation and `.env` rewriting).
- Outbound network access for the initial image pull. After install,
  Auditarr only reaches out to the update feed and (optionally) the
  plugin gallery.

## Quick install

```bash
# Download the release tarball, extract, then:
cd auditarr-1.0.0
./install.sh
```

The script will:

1. Verify Docker and the compose plugin.
2. Generate a 32-byte hex `secret_key` via `openssl rand -hex 32`.
3. Prompt for the first admin username, email, and password
   (≥16 chars, confirmed twice).
4. Prompt for any host paths you want mounted as libraries (read-only).
5. Write `.env` (mode 600) from `.env.example`.
6. Optionally write a `docker-compose.override.yml` with your library
   mounts.
7. Run `docker compose pull && docker compose up -d`.
8. Optionally start the `worker` profile.

After it finishes, open `http://localhost:8000` and sign in.

The installer is idempotent. Re-running it detects an existing `.env`
and offers to back it up before rewriting — useful when you want to
rotate the secret key or change the admin credentials.

## Compose stack at a glance

```
┌──────────┐    ┌──────────┐    ┌──────────┐
│   app    │───▶│ postgres │    │  redis   │
│ (FastAPI)│    │   :5432  │    │   :6379  │
└────┬─────┘    └──────────┘    └────┬─────┘
     │                                │
     │                                │
     ▼                                ▼
┌──────────┐                    ┌──────────┐
│  worker  │                    │  worker  │
│  (ARQ)   │◀───────────────────│ schedules│
└──────────┘                    └──────────┘
```

- **`app`** — FastAPI + the SPA, exposed on port 8000.
- **`postgres`** — primary data store. The default volume is
  `auditarr_pg_data`.
- **`redis`** — ARQ job queue + ephemeral cache.
- **`worker`** — the background worker profile. Start with
  `docker compose --profile worker up -d`. Optional but recommended:
  without it, scheduled jobs, optimization runs, and the housekeeping
  trim won't fire.

## Configuration reference

All configuration lives in `.env`. Keys not in this list are documented
in `.env.example`.

| Key | Purpose | Default |
|-----|---------|---------|
| `AUDITARR_SECRET_KEY` | Signing key for JWTs and AES-GCM. **Must be ≥16 chars.** Rotating this invalidates every refresh token. | (no default; required) |
| `AUDITARR_DATABASE_URL` | SQLAlchemy URL. The compose file points at the bundled Postgres. | `postgresql+asyncpg://...` |
| `AUDITARR_REDIS_URL` | ARQ queue + cache backend. | `redis://redis:6379/0` |
| `AUDITARR_BOOTSTRAP_ADMIN_USERNAME` | Username for the first admin user, created on initial boot. | unset |
| `AUDITARR_BOOTSTRAP_ADMIN_EMAIL` | Email for the first admin user. | `<username>@auditarr.local` |
| `AUDITARR_BOOTSTRAP_ADMIN_PASSWORD` | Password for the first admin user. | unset |
| `AUDITARR_UPDATE_FEED_URL` | Where the updater polls for new releases. | GitHub Releases |
| `AUDITARR_UPDATE_CHECK_INTERVAL_MINUTES` | How often the worker polls the feed. | `60` |
| `AUDITARR_PLUGIN_GALLERY_URL` | Manifest listing community plugins. Empty disables the UI. | GitHub raw |
| `AUDITARR_HOUSEKEEPING_DELIVERY_RETENTION_DAYS` | Trim window for `notification_deliveries`. `0` disables. | `30` |
| `AUDITARR_HOUSEKEEPING_UPDATE_CHECK_RETENTION_DAYS` | Trim window for `update_checks`. | `90` |
| `AUDITARR_HOUSEKEEPING_RULE_EVALUATION_RETENTION_DAYS` | Trim window for `rule_evaluations`. `0` = kept indefinitely. | `0` |
| `AUDITARR_HOUSEKEEPING_JOB_RUN_RETENTION_DAYS` | Trim window for `job_runs`. | `60` |
| `AUDITARR_AUTH_RATE_LIMIT_ATTEMPTS` | Max auth attempts per IP per window. `0` disables. | `10` |
| `AUDITARR_AUTH_RATE_LIMIT_WINDOW_SECONDS` | Sliding-window length for the rate limiter. | `300` |
| `AUDITARR_PLUGIN_DIR` | Where user plugins are loaded from. | `./plugins` |
| `AUDITARR_ENV` | `production`, `staging`, or `development`. Controls HSTS + dev features. | `production` |

The installer doesn't ask for every key — most are fine at their
defaults. Tweak `.env` directly if you need to.

## Library mounts

Auditarr reads your media library through a read-only bind mount.

If you ran the installer and added paths, they're already in
`docker-compose.override.yml`:

```yaml
services:
  app:
    volumes:
      - /srv/media/movies:/mnt/library-1:ro
      - /srv/media/tv:/mnt/library-2:ro
```

In the UI, **Settings → Libraries → Add** points each library at the
*container* path (e.g. `/mnt/library-1`), not the host path. Read-only
is recommended; Auditarr never needs to write to your library.

## Updater helper (optional)

Auditarr's apply flow writes a sentinel file the host watches; a tiny
helper script reads the sentinel and runs `docker compose pull && up -d`
on the host. This avoids mounting the docker socket into the
container.

```bash
sudo cp docker/updater/auditarr-update.service /etc/systemd/system/
sudo $EDITOR /etc/systemd/system/auditarr-update.service   # set paths
sudo systemctl daemon-reload
sudo systemctl enable --now auditarr-update
journalctl -u auditarr-update -f
```

See **Updates** (`docs/updater/overview.md`) for the sentinel protocol
and rollback flow.

## Manual setup

If you'd rather not use `install.sh`:

```bash
# 1. Generate a secret key
openssl rand -hex 32

# 2. Copy and edit .env
cp .env.example .env
$EDITOR .env

# 3. Set bootstrap admin env vars in .env
#    AUDITARR_BOOTSTRAP_ADMIN_USERNAME=admin
#    AUDITARR_BOOTSTRAP_ADMIN_EMAIL=admin@yourdomain.example
#    AUDITARR_BOOTSTRAP_ADMIN_PASSWORD=...

# 4. Add library mounts to docker-compose.override.yml if you want any

# 5. Bring it up
docker compose pull
docker compose up -d
docker compose --profile worker up -d
```

## Troubleshooting

**The first boot says "no users exist and bootstrap env vars are
unset".** Set `AUDITARR_BOOTSTRAP_ADMIN_USERNAME`, `_EMAIL`, and
`_PASSWORD` in `.env`, then restart: `docker compose restart app`.

**`secret_key must be at least 16 characters`.** Regenerate with
`openssl rand -hex 32` and replace the value in `.env`. Then restart.

**The worker isn't picking up jobs.** Make sure you started the
worker profile:

```bash
docker compose --profile worker ps
docker compose --profile worker up -d
```

Without the worker, schedules, optimization jobs, the update feed
check, and housekeeping all sit idle.

**Rate limiting locked me out.** The default is 10 attempts per
5-minute window per client IP. Wait it out or set
`AUDITARR_AUTH_RATE_LIMIT_ATTEMPTS=0` in `.env` temporarily.

**I rotated `AUDITARR_SECRET_KEY` and everyone got logged out.**
Expected. The key signs JWTs and encrypts integration secrets; rotating
it invalidates existing tokens and renders existing encrypted secrets
unreadable. Either avoid rotating, or rotate during a planned outage
and accept re-entering integration credentials.

## Upgrading

The updater handles in-place upgrades: see **Updates** in the help
drawer. If you'd rather upgrade manually:

```bash
git pull        # if you cloned the repo, otherwise extract the new release
docker compose pull
docker compose up -d
```

Database migrations run on container start. The Stage 0..N migration
chain is forward-only — Auditarr does not support downgrading a Postgres
database that's been upgraded across releases.

## What's NOT in v1.0

- **TLS termination.** Put a reverse proxy (Caddy, nginx, Traefik) in
  front of port 8000.
- **Multi-tenant.** One Auditarr instance serves one operator team.
- **Native (non-Docker) packaging.** The compose stack is the only
  supported installation path.
- **High availability.** Single instance, single Postgres. Use volume
  backups; Auditarr is not designed for active-active.
