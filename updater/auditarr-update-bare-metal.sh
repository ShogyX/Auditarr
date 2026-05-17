#!/usr/bin/env bash
# Auditarr updater helper (bare-metal install).
#
# Counterpart to ``docker/updater/auditarr-update.sh``. Same sentinel
# /status protocol — different consumer.
#
# This script runs on the host alongside the systemd units installed
# by ``install-bare-metal.sh``. It watches
# ``/var/lib/auditarr/updater/apply.request``, and when one shows up:
#
#   1. Downloads the release tarball for the requested version from
#      ``$AUDITARR_RELEASE_TARBALL_URL`` (a printf-style template with
#      ``%s`` for the version).
#   2. Verifies the tarball SHA256 if a checksum URL is configured.
#   3. Extracts to a staging dir under /var/lib/auditarr/updater/staging.
#   4. Stops auditarr-api and auditarr-worker.
#   5. Rsyncs backend/ and frontend/ over /opt/auditarr/, preserving
#      the venv (we re-install deps separately) and never touching
#      /etc/auditarr or /var/lib/auditarr (DB data + secrets).
#   6. Refreshes the venv via ``pip install -e .`` (picks up new deps).
#   7. Runs ``alembic upgrade head``.
#   8. Starts auditarr-api and auditarr-worker.
#   9. Writes the status file.
#
# Failure path: on any failure the script tries to bring the *old*
# services back up (the original /opt/auditarr tree is preserved at
# /var/lib/auditarr/updater/rollback/ during the swap).
#
# Run this script under a systemd service alongside the API + worker.
# An example unit is installed by install-bare-metal.sh as
# auditarr-update-watcher.service.

set -euo pipefail

# ── Config knobs (all overridable via the service's EnvironmentFile) ──
STATE_DIR="${AUDITARR_STATE_DIR:-/var/lib/auditarr}"
APP_HOME="${AUDITARR_HOME:-/opt/auditarr}"
CONFIG_DIR="${AUDITARR_CONFIG_DIR:-/etc/auditarr}"
VENV_DIR="${AUDITARR_VENV_DIR:-${APP_HOME}/venv}"
APP_USER="${AUDITARR_USER:-auditarr}"
APP_GROUP="${AUDITARR_GROUP:-auditarr}"

# ``%s`` is substituted with the requested version, e.g.
#   "https://github.com/ShogyX/Auditarr/releases/download/v%s/auditarr-%s.tar.gz"
# Operators on private mirrors set this to point at their artifact store.
#
# v1.8.2: if AUDITARR_RELEASE_TARBALL_URL is unset, fall back to
# GitHub's auto-generated source-tarball URL for the configured
# update-feed repo. This means the bare-metal apply path works
# out of the box for any deployment whose feed URL points at a
# real GitHub repo — operators no longer have to manually set
# the tarball URL just to enable the update workflow.
RELEASE_URL_TEMPLATE="${AUDITARR_RELEASE_TARBALL_URL:-}"
CHECKSUM_URL_TEMPLATE="${AUDITARR_RELEASE_CHECKSUM_URL:-}"

# Feed URL: used only to derive the default tarball URL when
# RELEASE_URL_TEMPLATE is empty. We parse it lazily — if the feed
# URL isn't a github.com URL, we leave RELEASE_URL_TEMPLATE empty
# and the apply will surface a clear "URL not configured" error.
UPDATE_FEED_URL="${AUDITARR_UPDATE_FEED_URL:-}"

POLL_INTERVAL="${AUDITARR_UPDATE_POLL_SECONDS:-5}"

UPDATER_DIR="${STATE_DIR}/updater"
SENTINEL="${UPDATER_DIR}/apply.request"
STATUS="${UPDATER_DIR}/apply.status"
STAGING_DIR="${UPDATER_DIR}/staging"
ROLLBACK_DIR="${UPDATER_DIR}/rollback"

mkdir -p "$UPDATER_DIR" "$STAGING_DIR" "$ROLLBACK_DIR"
chown -R "$APP_USER:$APP_GROUP" "$UPDATER_DIR"

log() {
    printf '[auditarr-update] %s %s\n' "$(date -Iseconds)" "$*"
}

# Write status atomically by going through a tmp file so the backend
# never reads a half-written JSON document.
write_status() {
    local apply_id="$1" status="$2" detail="$3" error="${4:-}"
    local tmpf
    tmpf="$(mktemp "${STATUS}.XXXXXX")"
    {
        printf '{'
        printf '"apply_id":"%s",' "$apply_id"
        printf '"status":"%s",' "$status"
        printf '"detail":%s' "$(printf '%s' "$detail" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')"
        if [[ -n "$error" ]]; then
            printf ',"error":%s' "$(printf '%s' "$error" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')"
        fi
        printf '}'
    } > "$tmpf"
    chown "$APP_USER:$APP_GROUP" "$tmpf"
    mv -f "$tmpf" "$STATUS"
}

# Pull a URL with curl, retry up to 3 times. Honor template ``%s``
# substitution. Returns 0 on success, non-zero on failure.
fetch() {
    local template="$1" version="$2" dest="$3"
    local url
    # shellcheck disable=SC2059
    url="$(printf "$template" "$version" "$version" "$version")"
    log "fetching $url"
    curl --silent --show-error --fail --location \
         --retry 3 --retry-delay 2 \
         --output "$dest" "$url"
}

apply_update() {
    local apply_id="$1" to_version="$2"

    log "apply requested: id=$apply_id to=$to_version"

    # v1.8.2: derive a default RELEASE_URL_TEMPLATE from the feed URL
    # if the operator hasn't set one explicitly. Recognised feed shape:
    # ``https://api.github.com/repos/<owner>/<repo>/releases/latest``.
    # We map it to GitHub's auto-generated source tarball:
    #   https://github.com/<owner>/<repo>/archive/refs/tags/v<ver>.tar.gz
    # This means the bare-metal apply path works out of the box for any
    # ``api.github.com/repos/...`` feed without the operator needing to
    # configure anything beyond the feed URL.
    if [[ -z "$RELEASE_URL_TEMPLATE" && "$UPDATE_FEED_URL" =~ ^https://api\.github\.com/repos/([^/]+)/([^/]+)/releases/latest$ ]]; then
        local owner="${BASH_REMATCH[1]}"
        local repo="${BASH_REMATCH[2]}"
        RELEASE_URL_TEMPLATE="https://github.com/${owner}/${repo}/archive/refs/tags/v%s.tar.gz"
        log "derived release URL template from feed: $RELEASE_URL_TEMPLATE"
    fi

    if [[ -z "$RELEASE_URL_TEMPLATE" ]]; then
        write_status "$apply_id" "failed" \
            "AUDITARR_RELEASE_TARBALL_URL is not configured and could not be derived from the feed URL." \
            "Set AUDITARR_RELEASE_TARBALL_URL explicitly in /etc/auditarr/updater.env, or set AUDITARR_UPDATE_FEED_URL to a GitHub api.github.com/repos/.../releases/latest URL so the watcher can derive one."
        return
    fi

    write_status "$apply_id" "running" "Downloading release tarball"

    local tarball="${STAGING_DIR}/auditarr-${to_version}.tar.gz"
    rm -f "$tarball"
    if ! fetch "$RELEASE_URL_TEMPLATE" "$to_version" "$tarball" 2>&1; then
        write_status "$apply_id" "failed" \
            "Download failed for version $to_version" \
            "Check that AUDITARR_RELEASE_TARBALL_URL is reachable and the version exists."
        return
    fi

    # Optional checksum verification.
    if [[ -n "$CHECKSUM_URL_TEMPLATE" ]]; then
        write_status "$apply_id" "running" "Verifying checksum"
        local checksum_file="${STAGING_DIR}/auditarr-${to_version}.sha256"
        rm -f "$checksum_file"
        if ! fetch "$CHECKSUM_URL_TEMPLATE" "$to_version" "$checksum_file"; then
            write_status "$apply_id" "failed" \
                "Checksum download failed" \
                "Set AUDITARR_RELEASE_CHECKSUM_URL to empty to skip verification."
            return
        fi
        # The checksum file is typically "<hex>  <filename>". We only
        # care about the hex part.
        local expected actual
        expected="$(awk '{print $1}' "$checksum_file" | head -1)"
        actual="$(sha256sum "$tarball" | awk '{print $1}')"
        if [[ "$expected" != "$actual" ]]; then
            write_status "$apply_id" "failed" \
                "Checksum mismatch" \
                "Expected $expected, got $actual. Aborting apply."
            return
        fi
    fi

    # Extract to a versioned staging subdir so a botched apply doesn't
    # leave half-written files in /opt/auditarr.
    write_status "$apply_id" "running" "Extracting tarball"
    local extract_dir="${STAGING_DIR}/auditarr-${to_version}"
    rm -rf "$extract_dir"
    mkdir -p "$extract_dir"
    if ! tar -C "$extract_dir" --strip-components=1 -xzf "$tarball"; then
        write_status "$apply_id" "failed" \
            "Extraction failed" \
            "Tarball may be corrupt. Re-download manually to inspect."
        return
    fi

    # Sanity-check the extracted layout matches the install-bare-metal
    # tarball shape.
    if [[ ! -f "$extract_dir/backend/pyproject.toml" ]]; then
        write_status "$apply_id" "failed" \
            "Extracted tarball doesn't look like an Auditarr release" \
            "backend/pyproject.toml missing — verify AUDITARR_RELEASE_TARBALL_URL."
        return
    fi

    # Snapshot the current /opt/auditarr so we can roll back. We move
    # rather than copy to keep the apply fast — typical deployments
    # have a few hundred MB under /opt/auditarr/venv that we DON'T
    # want to copy.
    write_status "$apply_id" "running" "Backing up current install"
    rm -rf "${ROLLBACK_DIR}/backend" "${ROLLBACK_DIR}/frontend" "${ROLLBACK_DIR}/plugins"
    cp -a "$APP_HOME/backend" "${ROLLBACK_DIR}/backend"
    cp -a "$APP_HOME/frontend" "${ROLLBACK_DIR}/frontend"
    cp -a "$APP_HOME/plugins" "${ROLLBACK_DIR}/plugins" 2>/dev/null || true

    # Stop services before swapping files. Doing this AFTER the
    # download means the API stays responsive during the long step
    # (good UX — the UI's apply spinner stays connected).
    write_status "$apply_id" "running" "Stopping services"
    systemctl stop auditarr-api auditarr-worker || {
        write_status "$apply_id" "failed" \
            "Couldn't stop services" \
            "Check: journalctl -u auditarr-api -u auditarr-worker"
        return
    }

    # Swap files. rsync preserves permissions and removes files no
    # longer in the new release.
    write_status "$apply_id" "running" "Installing new release"
    if ! rsync -a --delete \
              --exclude='__pycache__' --exclude='*.pyc' \
              "$extract_dir/backend/" "$APP_HOME/backend/"; then
        rollback_and_fail "$apply_id" "rsync backend failed"
        return
    fi
    if ! rsync -a --delete "$extract_dir/frontend/dist/" "$APP_HOME/frontend/" 2>/dev/null; then
        # Fall back to frontend/ if dist/ isn't separately shipped.
        if ! rsync -a --delete "$extract_dir/frontend/" "$APP_HOME/frontend/"; then
            rollback_and_fail "$apply_id" "rsync frontend failed"
            return
        fi
    fi
    if [[ -d "$extract_dir/backend/plugins" ]]; then
        rsync -a --delete \
              --exclude='__pycache__' \
              "$extract_dir/backend/plugins/" "$APP_HOME/plugins/" || true
    fi
    chown -R "$APP_USER:$APP_GROUP" "$APP_HOME"

    # Refresh venv dependencies. ``pip install -e .`` is idempotent
    # and cheap when nothing changed.
    write_status "$apply_id" "running" "Refreshing Python dependencies"
    if ! sudo -u "$APP_USER" "$VENV_DIR/bin/pip" install --quiet -e "$APP_HOME/backend"; then
        rollback_and_fail "$apply_id" "pip install failed"
        return
    fi

    # Run migrations.
    write_status "$apply_id" "running" "Running database migrations"
    if ! sudo -u "$APP_USER" \
              bash -c "set -a; . $CONFIG_DIR/auditarr.env; set +a; $VENV_DIR/bin/alembic -c $APP_HOME/backend/alembic.ini upgrade head"; then
        rollback_and_fail "$apply_id" "alembic upgrade failed"
        return
    fi

    # Start services back up.
    write_status "$apply_id" "running" "Starting services"
    if ! systemctl start auditarr-api auditarr-worker; then
        rollback_and_fail "$apply_id" "services failed to start after update"
        return
    fi

    # Wait briefly for the API to actually bind, so we don't report
    # success on a unit that's about to crashloop.
    sleep 3
    if ! systemctl is-active --quiet auditarr-api; then
        rollback_and_fail "$apply_id" \
            "auditarr-api is not active after start (check journalctl)"
        return
    fi

    log "apply complete: $to_version"
    write_status "$apply_id" "completed" \
        "Upgraded to $to_version. Migrations applied, services restarted."

    # Clean up staging on success.
    rm -rf "$extract_dir" "$tarball"
}

rollback_and_fail() {
    local apply_id="$1" reason="$2"
    log "ROLLBACK: $reason"

    # Restore the previous /opt/auditarr.
    if [[ -d "${ROLLBACK_DIR}/backend" ]]; then
        rsync -a --delete "${ROLLBACK_DIR}/backend/" "$APP_HOME/backend/" || true
    fi
    if [[ -d "${ROLLBACK_DIR}/frontend" ]]; then
        rsync -a --delete "${ROLLBACK_DIR}/frontend/" "$APP_HOME/frontend/" || true
    fi
    if [[ -d "${ROLLBACK_DIR}/plugins" ]]; then
        rsync -a --delete "${ROLLBACK_DIR}/plugins/" "$APP_HOME/plugins/" || true
    fi
    chown -R "$APP_USER:$APP_GROUP" "$APP_HOME"

    # Best-effort restart of the old version.
    systemctl start auditarr-api auditarr-worker 2>/dev/null || true

    write_status "$apply_id" "failed" \
        "Update failed: $reason. Rolled back to previous release." \
        "Inspect: journalctl -u auditarr-api -u auditarr-worker; also tail $UPDATER_DIR for details."
}

log "watching $SENTINEL (interval=${POLL_INTERVAL}s)"
log "release URL template: ${RELEASE_URL_TEMPLATE:-<not configured>}"

while true; do
    if [[ -f "$SENTINEL" ]]; then
        payload=$(<"$SENTINEL")
        apply_id=$(printf '%s' "$payload" \
            | python3 -c 'import json,sys;print(json.load(sys.stdin).get("apply_id",""))' 2>/dev/null || echo "")
        to_version=$(printf '%s' "$payload" \
            | python3 -c 'import json,sys;print(json.load(sys.stdin).get("to_version",""))' 2>/dev/null || echo "")

        if [[ -z "$apply_id" || -z "$to_version" ]]; then
            log "malformed sentinel; ignoring"
            rm -f "$SENTINEL"
        else
            # Remove the request before doing the work so a slow
            # apply doesn't trigger a re-run on the next tick.
            rm -f "$SENTINEL"
            apply_update "$apply_id" "$to_version" || true
        fi
    fi
    sleep "$POLL_INTERVAL"
done
