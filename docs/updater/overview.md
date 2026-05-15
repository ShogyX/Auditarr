---
id: updater/overview
title: Updates
category: updates
tags: [updater, releases, docker, rollback]
summary: How Auditarr checks for new versions and applies them.
help_context: [help.docs]
related: [getting-started/installation]
---

# Updates

Auditarr ships as a Docker image. The updater is a small surface around
`docker compose pull && docker compose up -d`: a configurable feed
poll, a sentinel-file bridge to a host-side helper that does the actual
work, and an audit log of every check and apply.

## Feed configuration

By default the updater polls the project's GitHub Releases JSON:

```
AUDITARR_UPDATE_FEED_URL=https://api.github.com/repos/auditarr/auditarr/releases/latest
```

Two response shapes are accepted automatically:

**GitHub Releases** — the default. The updater reads `tag_name` (with
an optional leading `v` stripped) and `body` (the release notes).

**Generic** — any JSON object with `version` and optional `changelog`
fields. Self-hosted mirrors should emit this shape:

```json
{
  "version": "1.4.0",
  "changelog": "Big rewrite of the optimization queue.\n..."
}
```

Shape selection is done by introspecting the response — `tag_name` →
GitHub, `version` → generic — so swapping mirrors needs no code
change.

## Check cadence

The ARQ worker runs a `update_check_tick` cron every minute. It checks
the feed only when `AUDITARR_UPDATE_CHECK_INTERVAL_MINUTES` (default
60) has elapsed since the last successful or failed check. Manually
forcing a check is `POST /api/v1/updater/check` (admin) or the **Check
now** button in **Help & updates**.

Each check writes one `update_checks` row. The newest row with `ok=true`
is the source of truth for "is there an update available". The
comparison rules:

- Pure semver `MAJOR.MINOR.PATCH` compares numerically.
- A release version beats a prerelease with the same numeric trio
  (`1.2.0` > `1.2.0-rc.1`).
- The dev sentinel `0.0.0-dev` is older than every real release, so dev
  builds always see "update available". This is intentional — it nudges
  contributors to test against the shipped release flow.
- Unknown shapes fall back to "different = upgrade", which keeps the
  notification visible if you pin an unusual tag.

## Applying an update

Containers cannot `docker compose` themselves without mounting the
docker socket, which is a much bigger attack surface than the updater
needs. So the apply path is split:

1. Operator clicks **Apply** (or hits `POST /api/v1/updater/apply`).
2. The updater writes a sentinel JSON file at
   `./data/updater/apply.request` and persists an `update_applies`
   row in status `requested`.
3. The host-side helper (`docker/updater/auditarr-update.sh`) watches
   that path. When it sees the sentinel, it deletes it, runs
   `docker compose pull && docker compose up -d <service>`, and writes
   a status JSON to `./data/updater/apply.status`.
4. On the next cron tick the updater consumes the status file and
   transitions the row to `completed` or `failed`, emitting
   `update.installed` or `update.failed` on the event bus.

### Setting up the helper

The helper ships with two files:

- `docker/updater/auditarr-update.sh` — the polling loop. Depends only
  on `bash`, `docker`, and `python3` (for tiny JSON escapes).
- `docker/updater/auditarr-update.service` — a systemd unit you can
  drop into `/etc/systemd/system/`.

Required env:

| Variable | Purpose |
|----------|---------|
| `AUDITARR_DATA_DIR` | Host path bound to `/app/data` in the container |
| `AUDITARR_COMPOSE_FILE` | Path to your `docker-compose.yml` |
| `AUDITARR_COMPOSE_SERVICE` | Service name to pull + recreate (default `app`) |
| `AUDITARR_UPDATE_POLL_SECONDS` | How often to check the sentinel (default `5`) |

Quick install:

```bash
sudo cp docker/updater/auditarr-update.service /etc/systemd/system/
# edit the file to point at your installation paths
sudo systemctl daemon-reload
sudo systemctl enable --now auditarr-update
journalctl -u auditarr-update -f
```

## Rollback

When an apply completes successfully, the `update_applies` row
captures both `from_version` and `to_version`. Click **Roll back** in
the UI (or `POST /api/v1/updater/applies/{id}/rollback`) and the
updater marks that row `rolled_back` and requests a fresh apply
targeting the old `from_version`. The host helper picks it up the same
way as any other apply.

Operators are responsible for making sure the previous image tag still
exists on the registry; the updater does not pin tags itself.

## Endpoint surface

| Path | Purpose |
|------|---------|
| `GET /api/v1/updater/status` | Combined view: installed/latest/has_update/recent-check |
| `POST /api/v1/updater/check` | Force an immediate feed check (admin) |
| `GET /api/v1/updater/checks` | Recent check history |
| `POST /api/v1/updater/apply` | Request an apply to a target version (admin) |
| `GET /api/v1/updater/applies` | Recent apply history |
| `POST /api/v1/updater/applies/{id}/rollback` | Roll back a completed apply (admin) |
| `GET /api/v1/system/version` | Lightweight version probe used by the sidebar |

## What's NOT in Stage 11

- **Auto-apply**. The updater never deploys without an operator click.
  Even with a passing healthcheck, automatic deployment of new code
  isn't something a self-hosted box should do at 3am unattended.
- **Multi-container coordination**. The helper recreates one compose
  service (default `app`). If you've stitched extra workers in front,
  set `AUDITARR_COMPOSE_SERVICE` per environment or run the helper
  multiple times.
- **Image signature verification**. The updater trusts the registry the
  compose file points at. If you need supply-chain assurance, use a
  private registry with cosign and configure containerd appropriately.
