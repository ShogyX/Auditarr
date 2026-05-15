# Bare-metal install — recovery from a failed first run

If your install crashed with messages like:

```
file has vanished: "/opt/auditarr/frontend/dist/index.html"
rsync warning: some files vanished before they could be transferred (code 24)
```

…then you hit the bug fixed in this release. The cause was running
the installer from inside its install target (typically `/opt/auditarr`).
The build step produced output in the same directory the installer then
tried to rsync `--delete` *to*, which wiped the output mid-transfer.

The current installer detects this case up front and refuses to run.
The recovery steps below get you to a clean install from the partial
state.

## Recovery steps

### 1. Stop and remove the rogue auditarr.service (if present)

The systemd output from the failed install may show something like:

```
auditarr.service - Auditarr Python Server
  ExecStart=/usr/bin/python3 /home/Auditarr/server.py
  Process: ... (code=exited, status=1/FAILURE)
```

This is **not from this installer** — it's left over from an
unrelated project on the box. This installer creates
`auditarr-api.service` and `auditarr-worker.service` (note the
hyphens). Remove the rogue unit:

```bash
sudo systemctl disable --now auditarr.service
sudo rm -f /etc/systemd/system/auditarr.service
sudo systemctl daemon-reload
```

### 2. Clean up the partial install directory

The build artifacts under `/opt/auditarr` are in a half-rsynced
state. The safest reset is to clear the application code (but keep
your data directories — `/var/lib/auditarr` and `/var/log/auditarr`
are not touched by this):

```bash
sudo rm -rf /opt/auditarr/backend
sudo rm -rf /opt/auditarr/frontend
sudo rm -rf /opt/auditarr/plugins
sudo rm -rf /opt/auditarr/.venv
sudo rm -rf /opt/auditarr/install-bare-metal.sh
sudo rm -rf /opt/auditarr/*.tar.gz   # if you extracted there too
```

If you don't have any prior data in `/var/lib/auditarr` you can
also clear it — but if PostgreSQL has any auditarr data it's in
`/var/lib/postgresql/`, not here.

### 3. Extract the release tarball to a fresh location

**Do not extract into `/opt/auditarr` again.** Extract somewhere
neutral so the installer's build step is separate from its
install target.

```bash
mkdir -p /tmp/auditarr-release
tar -xzf auditarr-*.tar.gz -C /tmp/auditarr-release --strip-components=1
cd /tmp/auditarr-release
```

### 4. Run the installer

```bash
sudo ./install-bare-metal.sh
```

The new same-directory guard will refuse the previous broken
layout up front with a clear error pointing to step 3 above. If
you've followed the steps in order, this run should complete
through every phase: system packages (mostly already installed),
service user, application files, Python venv, PostgreSQL DB,
migrations, systemd units, optional Nginx.

### 5. Verify the right services are running

```bash
sudo systemctl status auditarr-api.service auditarr-worker.service
```

Both should be `active (running)`. The old `auditarr.service`
should no longer exist:

```bash
sudo systemctl list-unit-files 'auditarr*.service'
```

Expected output (note the hyphens — `auditarr.service` should NOT
appear):

```
auditarr-api.service              enabled
auditarr-worker.service           enabled
auditarr-update-watcher.service   enabled
```

### 6. Confirm the API is responding

```bash
curl -s http://127.0.0.1:8000/api/v1/health | jq .
```

Expected: `{"status": "ok"}`.

## Why this happened

The original installer had two latent bugs that combined into the
failure you saw:

1. It didn't check whether `SCRIPT_DIR` and `AUDITARR_HOME` were
   the same directory. They typically aren't — operators extract
   to `/tmp` or `~/auditarr-release/` — but extracting straight to
   `/opt/auditarr` was a footgun.

2. Its rsync calls used `--delete` without tolerance for exit code
   24 ("source files vanished"). With `set -euo pipefail` set, that
   exit code crashed the script even when the cause was benign.

The installer in this release adds:

- A same-directory guard that refuses to run when
  `SCRIPT_DIR == AUDITARR_HOME` (resolved through symlinks), with
  an explicit error pointing to the fix.
- A `safe_rsync` wrapper that refuses parent/child / same-dir
  source-destination pairs and treats exit 24 as a warning instead
  of fatal.
- A pre-flight check for a pre-existing conflicting
  `auditarr.service` unit, with a prompt to disable it.

Together these turn the original silent self-destruction into an
immediate, clearly-explained refusal.
