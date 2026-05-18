---
id: updater/overview
title: Updates
category: updates
tags: [updater, releases, docker, bare-metal, rollback]
summary: How Auditarr checks for new versions and applies them.
help_context: [help.docs]
related: [getting-started/installation, getting-started/install-bare-metal]
---

# Updates

Auditarr ships in two install modes, and each handles updates
differently:

| Install mode | Update path                                |
|--------------|--------------------------------------------|
| Bare-metal   | One-click **Apply** from the Updates panel |
| Docker       | Operator runs three commands on the host   |
| Unmanaged    | Operator's own config tool (Ansible, etc.) |

The updater always polls the configured feed and persists a check
audit log regardless of install mode — only the **apply** step
differs.

## Feed configuration

By default the updater polls the project's GitHub Releases JSON:

```
AUDITARR_UPDATE_FEED_URL=https://api.github.com/repos/ShogyX/Auditarr/releases/latest
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

## Check cadence

The ARQ worker runs an `update_check_tick` cron every minute. It
checks the feed only when `AUDITARR_UPDATE_CHECK_INTERVAL_MINUTES`
(default 60) has elapsed since the last successful or failed check.
Manually forcing a check is `POST /api/v1/updater/check` (admin) or
the **Check now** button in **Help & updates**.

Comparison rules:

- Pure semver `MAJOR.MINOR.PATCH` compares numerically.
- A release version beats a prerelease with the same numeric trio
  (`1.2.0` > `1.2.0-rc.1`).
- The dev sentinel `0.0.0-dev` is older than every real release, so
  dev builds always see "update available". This is intentional —
  it nudges contributors to test against the shipped release flow.
- Unknown shapes fall back to "different = upgrade", which keeps
  the notification visible if you pin an unusual tag.

## Applying an update — bare-metal

The installer lays down two systemd helpers alongside
`auditarr-api.service` and `auditarr-worker.service`:

- `auditarr-update-watcher.service` — long-running root daemon that
  watches `/var/lib/auditarr/updater/apply.request` for a sentinel
  file written by the API.
- `/opt/auditarr/updater/auditarr-update-preflight.sh` — standalone
  health-check script the watcher invokes first; you can also run
  it by hand to validate the host before clicking Apply.

When the operator clicks **Apply** (or hits `POST /api/v1/updater/apply`):

1. The backend persists an `update_applies` row in status `requested`
   and writes the sentinel file with the target version.
2. The watcher picks up the sentinel on its next poll (default 5s).
3. The watcher runs the preflight checklist — required binaries,
   systemd unit access, network reachability to the feed, disk
   space, writable paths, app user existence. On any failure the
   apply is marked `failed` with the preflight summary as detail,
   and the operator fixes the host and re-clicks.
4. Preflight pass → the watcher downloads the release tarball,
   verifies the optional checksum, extracts to a staging dir,
   snapshots `/opt/auditarr/{backend,frontend,plugins}` to a
   rollback dir, stops services with a hard timeout, then hands
   off to `install-bare-metal.sh --auto` from the extracted tree.
5. The installer does what it always did: file swap, venv refresh,
   `alembic upgrade head`, `systemctl restart auditarr-api
   auditarr-worker`, and a health probe. With `--auto` set, the
   installer exits non-zero if the API doesn't respond on
   `/api/v1/health` within 60s.
6. The watcher does its own post-install health poll for an
   additional 90s, then writes the status file with `completed` and
   the elapsed time.
7. The backend's next `update_check_tick` consumes the status file
   and transitions the row to `completed` (or `failed`), emitting
   `update.installed` / `update.failed` on the event bus.

**Failure → automatic rollback.** If any step fails, the watcher
restores the snapshot, restarts services on the old version, and
writes the status file with the tail of the apply log captured in
`/var/lib/auditarr/updater/last-apply.log`.

**Hang protection.** The watcher applies four layers of timeout so
a wedged step can't peg the apply indefinitely:

| Knob                                       | Default | Purpose                          |
|--------------------------------------------|---------|----------------------------------|
| `AUDITARR_DOWNLOAD_TIMEOUT`                | 600s    | curl wall clock per download     |
| `AUDITARR_STOP_SERVICES_TIMEOUT`           | 60s     | `systemctl stop` deadline        |
| `AUDITARR_INSTALLER_TIMEOUT`               | 1500s   | `install-bare-metal.sh --auto`   |
| `AUDITARR_HEALTH_CHECK_TIMEOUT`            | 90s     | post-install `/health` poll      |
| `AUDITARR_APPLY_DEADLINE_SECONDS`          | 1800s   | outer wall clock per apply       |
| `AUDITARR_UPDATE_APPLY_TIMEOUT_SECONDS`    | 900s    | backend DB reaper for wedged row |

On any timeout the apply is marked `failed` with the timeout reason
as the status detail, and services are restored from the snapshot.

### Running preflight by hand

```bash
sudo /opt/auditarr/updater/auditarr-update-preflight.sh
```

Prints a tabular pass/fail summary. Exit 0 on all-pass, non-zero
otherwise.

### Watcher logs

```bash
journalctl -u auditarr-update-watcher -f
# detailed installer output from the last apply:
sudo tail -f /var/lib/auditarr/updater/last-apply.log
```

### Enabling auto-updates

The watcher is installed automatically. By default it pulls release
tarballs from GitHub using the feed URL; for a private mirror or
non-GitHub source, set `AUDITARR_RELEASE_TARBALL_URL` in
`/etc/auditarr/updater.env`:

```
AUDITARR_RELEASE_TARBALL_URL=https://example.com/auditarr/v%s/auditarr-%s.tar.gz
AUDITARR_RELEASE_CHECKSUM_URL=https://example.com/auditarr/v%s/auditarr-%s.tar.gz.sha256
```

`%s` is replaced with the requested version. After editing,
`sudo systemctl restart auditarr-update-watcher`.

## Applying an update — Docker

Containers shouldn't be able to recreate themselves — doing so
requires mounting the Docker socket, which gives the container full
control over the host's Docker daemon and defeats container
isolation. So Docker installs are **manual**: the Updates panel
shows a copy-paste-ready command block instead of an Apply button,
and `POST /api/v1/updater/apply` returns 409 with a Docker-specific
error message.

The canonical update command sequence:

```bash
# In the directory containing your docker-compose.yml:
cd /path/to/auditarr
git pull origin main          # or: git fetch && git checkout v<new-version>
docker compose pull
docker compose up -d --force-recreate
docker compose ps             # confirm app + worker are 'running'
```

If you originally cloned from a fork or a non-default branch,
substitute accordingly. The Updates panel renders the same string
the API returns via `manual_apply_command` so docs and UI can't
drift.

## Applying an update — unmanaged

When `AUDITARR_UPDATE_INSTALL_MODE=unmanaged` (or the auto-detector
can't pin a mode), the API rejects apply requests with a 409 and
the UI grays out the Apply button. Use your config tool's normal
upgrade flow.

## Rollback

When an apply completes successfully, the `update_applies` row
captures both `from_version` and `to_version`. Click **Roll back**
in the UI (or `POST /api/v1/updater/applies/{id}/rollback`) and the
updater marks that row `rolled_back` and requests a fresh apply
targeting the previous `from_version`. The watcher picks it up the
same way as any other apply.

This is the **bare-metal** rollback flow — it only works for installs
where the watcher can actually run a downgrade. Docker installs
roll back the same way they upgrade: manually, by checking out the
previous tag and recreating the container.

## Endpoint surface

| Path | Purpose |
|------|---------|
| `GET /api/v1/updater/status` | Combined view: installed/latest/has_update/recent-check/install_mode/manual_apply_command |
| `POST /api/v1/updater/check` | Force an immediate feed check (admin) |
| `GET /api/v1/updater/checks` | Recent check history |
| `POST /api/v1/updater/apply` | Request an apply (admin; bare-metal only) |
| `GET /api/v1/updater/applies` | Recent apply history |
| `POST /api/v1/updater/applies/{id}/rollback` | Roll back a completed apply (admin) |
| `POST /api/v1/updater/applies/{id}/force-clear` | Force-clear a stuck apply (admin) |
| `GET /api/v1/system/version` | Lightweight version probe used by the sidebar |

## What's NOT in

- **Auto-apply without operator click.** The updater never deploys
  without explicit consent. Even with a passing healthcheck,
  automatic deployment of new code isn't something a self-hosted
  box should do at 3am unattended.
- **In-container Docker apply** (removed in v1.9.1). Containers
  recreating themselves required socket access; replacing the
  helper-script flow with `git pull && docker compose up -d
  --force-recreate` keeps the container's blast radius bounded.
- **Image signature verification.** The updater trusts the source
  the release URL (or compose file) points at. For supply-chain
  assurance, use a private mirror with checksums (and configure
  `AUDITARR_RELEASE_CHECKSUM_URL`).
