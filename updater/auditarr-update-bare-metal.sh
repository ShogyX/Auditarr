#!/usr/bin/env bash
# Auditarr updater helper (bare-metal install).
#
# v1.9.1 Stage 1.6 — rewritten to delegate the actual install work
# to ``install-bare-metal.sh --auto``. The pre-1.9.1 watcher
# reimplemented the install (rsync + pip + alembic + systemctl) with
# no timeouts on any blocking step, so a slow PyPI, a long migration,
# or a wedged gunicorn startup pegged the watcher forever and made
# the apply look like "running" in the UI while the host was stuck
# half-restarted. The new shape:
#
#   1. Run preflight (delegates to auditarr-update-preflight.sh).
#   2. Download the release tarball from the configured URL.
#   3. Verify checksum (optional, unchanged).
#   4. Extract to a versioned staging dir.
#   5. Snapshot $APP_HOME for rollback.
#   6. Stop services with a hard timeout (force-kill if needed).
#   7. chmod the installer and exec ``install-bare-metal.sh --auto``
#      under a wall-clock deadline. The installer handles file swap,
#      venv refresh, migrations, and the final systemctl restart —
#      one implementation, one place to maintain.
#   8. Poll /api/v1/health for liveness.
#   9. On failure, restore the snapshot, re-run the installer to
#      bring the old version back up, and report the captured log.
#
# Per-step timeouts + a global apply deadline (1800s default) mean
# the watcher can no longer hang indefinitely — the worst case is a
# failed status with a clear actionable detail. Run this script
# under a systemd service alongside the API + worker; an example
# unit is installed by install-bare-metal.sh as
# auditarr-update-watcher.service.

set -euo pipefail

# ── Config knobs (all overridable via the service's EnvironmentFile) ──
STATE_DIR="${AUDITARR_STATE_DIR:-/var/lib/auditarr}"
APP_HOME="${AUDITARR_HOME:-/opt/auditarr}"
CONFIG_DIR="${AUDITARR_CONFIG_DIR:-/etc/auditarr}"
APP_USER="${AUDITARR_USER:-auditarr}"
APP_GROUP="${AUDITARR_GROUP:-auditarr}"

RELEASE_URL_TEMPLATE="${AUDITARR_RELEASE_TARBALL_URL:-}"
CHECKSUM_URL_TEMPLATE="${AUDITARR_RELEASE_CHECKSUM_URL:-}"
UPDATE_FEED_URL="${AUDITARR_UPDATE_FEED_URL:-}"

POLL_INTERVAL="${AUDITARR_UPDATE_POLL_SECONDS:-5}"

# Per-step + global timeouts. The global deadline is the outer wall
# clock: if the entire apply takes longer than this, the watcher
# kills the subprocess tree and writes status=failed. Per-step
# timeouts catch the common hangs (systemctl wedge, installer hang)
# before the global deadline trips.
APPLY_DEADLINE_SECONDS="${AUDITARR_APPLY_DEADLINE_SECONDS:-1800}"
STOP_SERVICES_TIMEOUT="${AUDITARR_STOP_SERVICES_TIMEOUT:-60}"
INSTALLER_TIMEOUT="${AUDITARR_INSTALLER_TIMEOUT:-1500}"
HEALTH_CHECK_TIMEOUT="${AUDITARR_HEALTH_CHECK_TIMEOUT:-90}"
DOWNLOAD_TIMEOUT="${AUDITARR_DOWNLOAD_TIMEOUT:-600}"

# Bind-port the installer uses; defaults match install-bare-metal.sh.
# Used only for the post-install health probe.
LISTEN_PORT="${AUDITARR_LISTEN_PORT:-8000}"

UPDATER_DIR="${STATE_DIR}/updater"
SENTINEL="${UPDATER_DIR}/apply.request"
STATUS="${UPDATER_DIR}/apply.status"
STAGING_DIR="${UPDATER_DIR}/staging"
ROLLBACK_DIR="${UPDATER_DIR}/rollback"
APPLY_LOG="${UPDATER_DIR}/last-apply.log"

# Preflight script — lives next to this one on disk. The watcher
# sources it for the shared check function; operators can also run
# it standalone to validate a host before clicking Apply.
PREFLIGHT_SCRIPT="${AUDITARR_PREFLIGHT_SCRIPT:-$(dirname "$0")/auditarr-update-preflight.sh}"

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

# Pull a URL with curl, retry up to 3 times, with a hard wall clock.
# Honors template ``%s`` substitution. Returns 0 on success.
fetch() {
    local template="$1" version="$2" dest="$3"
    local url
    # shellcheck disable=SC2059
    url="$(printf "$template" "$version" "$version" "$version")"
    log "fetching $url"
    curl --silent --show-error --fail --location \
         --max-time "$DOWNLOAD_TIMEOUT" --connect-timeout 30 \
         --retry 3 --retry-delay 2 \
         --output "$dest" "$url"
}

# Derive the release URL from the feed URL if one wasn't set
# explicitly. Recognised feed shape:
#   https://api.github.com/repos/<owner>/<repo>/releases/latest
# We map it to GitHub's auto-generated source-tarball endpoint.
derive_release_url() {
    if [[ -n "$RELEASE_URL_TEMPLATE" ]]; then
        return 0
    fi
    if [[ "$UPDATE_FEED_URL" =~ ^https://api\.github\.com/repos/([^/]+)/([^/]+)/releases/latest$ ]]; then
        local owner="${BASH_REMATCH[1]}"
        local repo="${BASH_REMATCH[2]}"
        RELEASE_URL_TEMPLATE="https://github.com/${owner}/${repo}/archive/refs/tags/v%s.tar.gz"
        log "derived release URL template from feed: $RELEASE_URL_TEMPLATE"
    fi
}

# Run the preflight script. Returns 0 if the host is ready, non-zero
# with the script's tabular output on stdout otherwise. The watcher
# captures the output and surfaces it as the failure detail so the
# operator sees the exact blocker.
run_preflight() {
    if [[ ! -x "$PREFLIGHT_SCRIPT" ]]; then
        log "WARNING: preflight script not found or not executable: $PREFLIGHT_SCRIPT"
        log "skipping preflight; install-bare-metal.sh may still catch problems"
        return 0
    fi
    "$PREFLIGHT_SCRIPT"
}

# Stop services with a hard timeout. Plain ``systemctl stop`` blocks
# forever on a wedged unit; we use ``timeout`` to bound the wait and
# force-kill the units if they don't stop cleanly.
stop_services_with_deadline() {
    log "stopping auditarr-api + auditarr-worker (timeout=${STOP_SERVICES_TIMEOUT}s)"
    if timeout "$STOP_SERVICES_TIMEOUT" \
            systemctl stop auditarr-api auditarr-worker; then
        return 0
    fi
    log "systemctl stop exceeded ${STOP_SERVICES_TIMEOUT}s; sending SIGKILL"
    systemctl kill --signal=SIGKILL auditarr-api auditarr-worker 2>/dev/null || true
    sleep 2
    if systemctl is-active --quiet auditarr-api \
       || systemctl is-active --quiet auditarr-worker; then
        return 1
    fi
    return 0
}

# Poll the API health endpoint until it returns 200 or the deadline
# expires. Returns 0 on first successful response.
wait_for_api_health() {
    local deadline=$((SECONDS + HEALTH_CHECK_TIMEOUT))
    local url="http://127.0.0.1:${LISTEN_PORT}/api/v1/health"
    log "polling $url for up to ${HEALTH_CHECK_TIMEOUT}s"
    while (( SECONDS < deadline )); do
        if curl -sf --max-time 5 -o /dev/null "$url"; then
            return 0
        fi
        sleep 3
    done
    return 1
}

# Capture last N lines of the apply log for the failure detail. Bound
# the size so a runaway installer log doesn't blow up the status JSON.
log_tail() {
    if [[ -f "$APPLY_LOG" ]]; then
        tail -c 2048 "$APPLY_LOG" 2>/dev/null || true
    fi
}

rollback_and_fail() {
    local apply_id="$1" reason="$2"
    log "ROLLBACK: $reason"
    write_status "$apply_id" "running" "Rollback in progress: $reason"

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

    # Best-effort restart of the old version. Timeout-bounded so a
    # broken old version doesn't peg us in the rollback path too.
    timeout "$STOP_SERVICES_TIMEOUT" \
        systemctl start auditarr-api auditarr-worker 2>/dev/null || true

    local tail
    tail="$(log_tail)"
    write_status "$apply_id" "failed" \
        "Update failed: $reason. Rolled back to previous release." \
        "$tail"
}

apply_update() {
    local apply_id="$1" to_version="$2"
    local started=$SECONDS

    log "apply requested: id=$apply_id to=$to_version"
    : > "$APPLY_LOG"

    # ── Preflight ────────────────────────────────────────────────
    write_status "$apply_id" "running" "Running preflight checks"
    local preflight_out
    if ! preflight_out="$(run_preflight 2>&1)"; then
        log "preflight failed:"
        printf '%s\n' "$preflight_out" | tee -a "$APPLY_LOG"
        write_status "$apply_id" "failed" \
            "Preflight checks failed — see error for details." \
            "$preflight_out"
        return
    fi

    # ── Release URL ──────────────────────────────────────────────
    derive_release_url
    if [[ -z "$RELEASE_URL_TEMPLATE" ]]; then
        write_status "$apply_id" "failed" \
            "AUDITARR_RELEASE_TARBALL_URL is not configured and could not be derived from the feed URL." \
            "Set AUDITARR_RELEASE_TARBALL_URL explicitly in $CONFIG_DIR/updater.env, or set AUDITARR_UPDATE_FEED_URL to an api.github.com/repos/.../releases/latest URL."
        return
    fi

    # ── Download ─────────────────────────────────────────────────
    write_status "$apply_id" "running" "Downloading release tarball"
    local tarball="${STAGING_DIR}/auditarr-${to_version}.tar.gz"
    rm -f "$tarball"
    if ! fetch "$RELEASE_URL_TEMPLATE" "$to_version" "$tarball" >>"$APPLY_LOG" 2>&1; then
        write_status "$apply_id" "failed" \
            "Download failed for version $to_version" \
            "$(log_tail)"
        return
    fi

    # ── Optional checksum verification ───────────────────────────
    if [[ -n "$CHECKSUM_URL_TEMPLATE" ]]; then
        write_status "$apply_id" "running" "Verifying checksum"
        local checksum_file="${STAGING_DIR}/auditarr-${to_version}.sha256"
        rm -f "$checksum_file"
        if ! fetch "$CHECKSUM_URL_TEMPLATE" "$to_version" "$checksum_file" >>"$APPLY_LOG" 2>&1; then
            write_status "$apply_id" "failed" \
                "Checksum download failed" \
                "Set AUDITARR_RELEASE_CHECKSUM_URL to empty to skip verification."
            return
        fi
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

    # ── Extract ──────────────────────────────────────────────────
    write_status "$apply_id" "running" "Extracting tarball"
    local extract_dir="${STAGING_DIR}/auditarr-${to_version}"
    rm -rf "$extract_dir"
    mkdir -p "$extract_dir"
    if ! tar -C "$extract_dir" --strip-components=1 -xzf "$tarball" >>"$APPLY_LOG" 2>&1; then
        write_status "$apply_id" "failed" \
            "Extraction failed" \
            "$(log_tail)"
        return
    fi

    # Sanity-check the extracted layout matches what the installer
    # expects. install-bare-metal.sh assumes ``$PWD/backend`` and
    # ``$PWD/install-bare-metal.sh`` exist at exec time.
    if [[ ! -f "$extract_dir/install-bare-metal.sh" ]]; then
        write_status "$apply_id" "failed" \
            "Extracted tarball is missing install-bare-metal.sh" \
            "The tarball at $RELEASE_URL_TEMPLATE doesn't look like an Auditarr release. Check the URL."
        return
    fi
    if [[ ! -f "$extract_dir/backend/pyproject.toml" ]]; then
        write_status "$apply_id" "failed" \
            "Extracted tarball is missing backend/pyproject.toml" \
            "Verify AUDITARR_RELEASE_TARBALL_URL points at an Auditarr release tarball."
        return
    fi

    # ── Snapshot for rollback ────────────────────────────────────
    write_status "$apply_id" "running" "Snapshotting current install for rollback"
    rm -rf "${ROLLBACK_DIR}/backend" "${ROLLBACK_DIR}/frontend" "${ROLLBACK_DIR}/plugins"
    cp -a "$APP_HOME/backend" "${ROLLBACK_DIR}/backend"
    cp -a "$APP_HOME/frontend" "${ROLLBACK_DIR}/frontend"
    cp -a "$APP_HOME/plugins" "${ROLLBACK_DIR}/plugins" 2>/dev/null || true

    # ── Stop services ────────────────────────────────────────────
    # NB: the installer will restart these at the end. We stop first
    # so the installer's rsync into $APP_HOME/backend doesn't race
    # with the running gunicorn workers.
    write_status "$apply_id" "running" "Stopping services"
    if ! stop_services_with_deadline; then
        rollback_and_fail "$apply_id" \
            "could not stop services within ${STOP_SERVICES_TIMEOUT}s; SIGKILL did not work either"
        return
    fi

    # ── Hand off to installer ────────────────────────────────────
    write_status "$apply_id" "running" "Running install-bare-metal.sh --auto"
    chmod +x "$extract_dir/install-bare-metal.sh"
    local installer_rc=0
    if ! timeout "$INSTALLER_TIMEOUT" \
            bash "$extract_dir/install-bare-metal.sh" --auto >>"$APPLY_LOG" 2>&1; then
        installer_rc=$?
        rollback_and_fail "$apply_id" \
            "install-bare-metal.sh --auto failed (exit=$installer_rc)"
        return
    fi

    # ── Post-install health check ────────────────────────────────
    write_status "$apply_id" "running" "Probing API health endpoint"
    if ! wait_for_api_health; then
        rollback_and_fail "$apply_id" \
            "API didn't respond on /api/v1/health within ${HEALTH_CHECK_TIMEOUT}s after installer completed"
        return
    fi

    local elapsed=$((SECONDS - started))
    log "apply complete: $to_version (${elapsed}s)"
    write_status "$apply_id" "completed" \
        "Upgraded to $to_version in ${elapsed}s. Installer log: $APPLY_LOG"

    # Clean up staging on success. Keep rollback dir around — the
    # operator may want to roll back later via the UI.
    rm -rf "$extract_dir" "$tarball"
}

# Wrap apply_update with a wall-clock deadline. If the entire apply
# exceeds APPLY_DEADLINE_SECONDS, we kill the subprocess tree and
# mark the row failed. This is the watcher's safety net for the
# hang-on-restart class of bug — even if every per-step timeout
# silently passes, the outer clock catches it.
apply_with_deadline() {
    local apply_id="$1" to_version="$2"
    ( apply_update "$apply_id" "$to_version" ) &
    local pid=$!
    local elapsed=0
    while kill -0 "$pid" 2>/dev/null; do
        if (( elapsed >= APPLY_DEADLINE_SECONDS )); then
            log "apply exceeded deadline (${APPLY_DEADLINE_SECONDS}s); killing subprocess tree"
            pkill -KILL -P "$pid" 2>/dev/null || true
            kill -KILL "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
            rollback_and_fail "$apply_id" \
                "apply exceeded ${APPLY_DEADLINE_SECONDS}s deadline; subprocess tree killed"
            return
        fi
        sleep 5
        elapsed=$((elapsed + 5))
    done
    wait "$pid" 2>/dev/null || true
}

log "watching $SENTINEL (interval=${POLL_INTERVAL}s, deadline=${APPLY_DEADLINE_SECONDS}s)"
log "preflight: $PREFLIGHT_SCRIPT"

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
            apply_with_deadline "$apply_id" "$to_version" || true
        fi
    fi
    sleep "$POLL_INTERVAL"
done
