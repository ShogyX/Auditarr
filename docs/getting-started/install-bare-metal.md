---
id: getting-started/install-bare-metal
title: Bare-metal install (LXC / VM)
category: getting-started
tags: [install, lxc, vm, systemd, bare-metal, setup, getting-started]
summary: Install Auditarr on an LXC container or VM without Docker, using systemd services.
help_context: [getting-started.install-bare-metal]
related: [getting-started/installation, updater/overview]
---

# Bare-metal install (LXC / VM)

If you'd rather not run Docker — for example because Auditarr lives in a
Proxmox LXC container, on a small VM, or on a host where Docker
conflicts with your network setup — use `install-bare-metal.sh`.

It runs the same backend, worker, and frontend as the Docker image, but
under native systemd services with PostgreSQL and Redis installed
directly on the host. The data layout matches the
[Linux Filesystem Hierarchy Standard](https://refspecs.linuxfoundation.org/FHS_3.0/fhs/index.html):

```
/opt/auditarr/        application (backend, plugins, built frontend, venv)
/etc/auditarr/        environment file (secret key, DB URL, ...)
/var/lib/auditarr/    persistent state (caches, FFmpeg artifacts)
/var/log/auditarr/    log files (when not journaling to systemd)
```

## Requirements

- **Linux host** with systemd. Tested on Debian 12, Ubuntu 22.04, and
  Ubuntu 24.04. Other distros work if you have Python 3.12 available.
- **Root access** (the installer uses `sudo` internally for postgres
  setup and systemd unit installation).
- **Python 3.12** — Ubuntu 24.04 ships it; on Debian 12 you'll need
  deadsnakes or a custom apt source.
- **1 GB free RAM** at rest (Postgres + Redis + API + worker together).
- **Outbound network** for the initial package install. After that,
  Auditarr only reaches out to the update feed and the plugin gallery.

## Quick install

```bash
# Extract the release tarball, then:
cd auditarr-1.2.1
sudo ./install-bare-metal.sh
```

The installer walks you through:

1. **System packages** — Python 3.12, PostgreSQL, Redis, ffmpeg, nginx,
   build tools. Auto-detected via `apt`; skipped on unsupported distros
   with a prompt to install manually.
2. **Service user + directories** — creates `auditarr:auditarr` as a
   system user owning `/opt/auditarr` and `/var/lib/auditarr`.
3. **Postgres bootstrap** — creates an `auditarr` role with a generated
   password and an `auditarr` database it owns. Password lives only in
   `/etc/auditarr/auditarr.env` (mode 0640, group-readable).
4. **Environment file** — writes `/etc/auditarr/auditarr.env` with a
   fresh 48-byte secret key, the DB URL, and Redis URL.
5. **Migrations + admin** — runs `alembic upgrade head`, then prompts
   for the first admin's email/username/password.
6. **systemd units** — installs `auditarr-api.service` (gunicorn +
   uvicorn) and `auditarr-worker.service` (arq), enables, starts.
7. **nginx reverse proxy** — optional, prompts. Maps port 80 → the
   API's `127.0.0.1:8000`.

After the installer finishes, Auditarr is reachable at
`http://<host>:8000/` (or port 80 if you took the nginx option).

## Non-interactive installs

For IaC tooling (Ansible, Salt, Terraform `remote-exec`, etc.),
suppress prompts by setting these env vars:

```bash
sudo AUDITARR_NONINTERACTIVE=1 \
     AUDITARR_ADMIN_EMAIL=admin@example.com \
     AUDITARR_ADMIN_USERNAME=admin \
     AUDITARR_ADMIN_PASSWORD='use-a-strong-one' \
     AUDITARR_INSTALL_NGINX=yes \
     ./install-bare-metal.sh
```

Other knobs (with their defaults):

| Variable | Default | Effect |
|---|---|---|
| `AUDITARR_USER` | `auditarr` | Service user |
| `AUDITARR_HOME` | `/opt/auditarr` | Install root |
| `AUDITARR_CONFIG_DIR` | `/etc/auditarr` | Env file location |
| `AUDITARR_STATE_DIR` | `/var/lib/auditarr` | Persistent caches |
| `AUDITARR_PG_DB` | `auditarr` | Postgres database name |
| `AUDITARR_PG_USER` | `auditarr` | Postgres role name |
| `AUDITARR_PG_HOST` | `127.0.0.1` | If using an external Postgres |
| `AUDITARR_REDIS_URL` | `redis://127.0.0.1:6379/0` | If using an external Redis |
| `AUDITARR_LISTEN_HOST` | `127.0.0.1` | Bind the API here |
| `AUDITARR_LISTEN_PORT` | `8000` | Bind the API on this port |
| `AUDITARR_INSTALL_NGINX` | `prompt` | `yes` / `no` / `prompt` |

## What's running after install

```
$ systemctl status auditarr-api auditarr-worker
● auditarr-api.service - Auditarr API (gunicorn/uvicorn)
   Active: active (running)
● auditarr-worker.service - Auditarr background worker (arq)
   Active: active (running)
```

Logs go to the systemd journal:

```bash
journalctl -u auditarr-api -f
journalctl -u auditarr-worker -f
```

The API and worker share `/etc/auditarr/auditarr.env`. Edit the file,
then restart both:

```bash
sudo systemctl restart auditarr-api auditarr-worker
```

## CLI access

Auditarr ships a CLI for operational tasks (plugin discovery, manual
job runs, schema checks). Run it as the service user so it picks up
the right environment:

```bash
sudo -u auditarr bash -c \
  'set -a; . /etc/auditarr/auditarr.env; set +a; /opt/auditarr/venv/bin/auditarr --help'
```

Useful subcommands:

- `auditarr version` — print the running version
- `auditarr db-check` — verify the Postgres connection
- `auditarr redis-check` — verify the Redis connection
- `auditarr plugin-list` — list discovered plugins + their manifests
- `auditarr user count-admins` — print number of admin users
- `auditarr user bootstrap-admin` — create an admin (interactive
  password via `--password-from-env`)

## Updating

When a new release ships:

1. Download and extract the new tarball alongside the current one.
2. Stop the services:
   ```bash
   sudo systemctl stop auditarr-api auditarr-worker
   ```
3. Re-run the installer from the new tarball:
   ```bash
   cd auditarr-1.3.0
   sudo ./install-bare-metal.sh
   ```
   It detects the existing install, preserves the env file (and the
   secret key + admin user), refreshes the application files,
   re-runs migrations, and restarts the services.

### Automatic updates from the UI

The installer also lays down `auditarr-update-watcher.service`, a
small daemon that watches `/var/lib/auditarr/updater/apply.request`.
When you click **Apply update** in the UI, the backend writes a
sentinel file; the watcher reads it and runs the equivalent of the
manual flow above: download the new release tarball, snapshot the
current install, swap files, refresh deps, run migrations, restart
services. If anything fails, it rolls back to the snapshot.

**Auto-updates are OPT-IN.** The default
`/etc/auditarr/updater.env` ships with the relevant URLs commented
out. To enable:

1. Edit `/etc/auditarr/updater.env`:
   ```bash
   sudo nano /etc/auditarr/updater.env
   ```
2. Uncomment and set `AUDITARR_RELEASE_TARBALL_URL`:
   ```
   AUDITARR_RELEASE_TARBALL_URL=https://github.com/auditarr/auditarr/releases/download/v%s/auditarr-%s.tar.gz
   ```
   The `%s` placeholders are substituted with the requested version
   (e.g. `1.4.0`). If you mirror releases internally, point this at
   your artifact store instead.
3. Optionally also set `AUDITARR_RELEASE_CHECKSUM_URL` to enable
   SHA256 verification of the downloaded tarball:
   ```
   AUDITARR_RELEASE_CHECKSUM_URL=https://github.com/auditarr/auditarr/releases/download/v%s/auditarr-%s.tar.gz.sha256
   ```
4. Restart the watcher:
   ```bash
   sudo systemctl restart auditarr-update-watcher
   ```

The watcher's logs go to the systemd journal:

```bash
journalctl -u auditarr-update-watcher -f
```

### Install-mode override

The backend auto-detects which install environment it's running in
and surfaces it on `GET /api/v1/updater/status` as `install_mode`.
The UI uses this to disable the Apply button if the install can't
auto-update.

If you ever need to override the detection (e.g. running under an
unusual nested container that confuses the detector), set
`AUDITARR_UPDATE_INSTALL_MODE` in `/etc/auditarr/auditarr.env`:

- `auto` — default; detect at startup
- `docker` — pin to Docker
- `bare-metal` — pin to systemd (set by `install-bare-metal.sh`)
- `unmanaged` — disable the Apply button entirely; you'll update
  by hand. Useful when Auditarr is managed by Ansible or a similar
  config-management tool that handles upgrades externally.

## Uninstall

There's no automated uninstall — Auditarr is just a service user, a
directory tree, three systemd units, and a Postgres database. To
remove:

```bash
sudo systemctl disable --now auditarr-api auditarr-worker auditarr-update-watcher
sudo rm /etc/systemd/system/auditarr-api.service \
        /etc/systemd/system/auditarr-worker.service \
        /etc/systemd/system/auditarr-update-watcher.service
sudo systemctl daemon-reload
sudo rm -rf /opt/auditarr /var/lib/auditarr /var/log/auditarr /etc/auditarr
sudo userdel auditarr
sudo -u postgres dropdb auditarr
sudo -u postgres dropuser auditarr
```

## Troubleshooting

**"Python 3.12 is required" on Debian 12.** Debian 12 ships Python
3.11. Either upgrade to a distro that ships 3.12 (Ubuntu 24.04,
Debian 13 when it's available), or add a third-party apt source for
3.12 before running the installer.

**API service won't start.** Check the journal:
```bash
journalctl -u auditarr-api -n 100 --no-pager
```
The most common causes are a wrong DB URL in `/etc/auditarr/auditarr.env`
or Postgres not running. Verify with `auditarr db-check`.

**Migrations fail.** Read the Alembic output in the journal — it'll
name the migration and the offending SQL. If you're migrating from
an older install, ensure you upgraded one minor version at a time.

**ffmpeg / ffprobe not found.** Re-install: `sudo apt install -y
ffmpeg`. The scanner won't analyze any media until this works.

**Library mount paths.** If your media lives on a network share that
isn't mounted at install time, mount it (e.g. via `/etc/fstab`) and
then add the library through the UI. The path you give Auditarr is
whatever path the `auditarr` user can read.
