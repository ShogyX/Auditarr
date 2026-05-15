#!/usr/bin/env bash
# Auditarr updater helper.
#
# Watches the apply sentinel file the Auditarr container writes, runs
# `docker compose pull && up -d` against the configured compose file,
# and writes a status file the container picks back up.
#
# This script lives on the *host*, alongside the docker-compose.yml. It
# cannot live inside the Auditarr container because containers cannot
# `docker compose` themselves (you'd have to mount the docker socket,
# which is a much bigger attack surface than this fifteen-line script).
#
# Wiring:
#
#   1. Run this script as a systemd service or under tmux/screen on the
#      Docker host.
#   2. Set AUDITARR_DATA_DIR to the same path you bind-mount into the
#      container at `/app/data`. The script polls
#      ``${AUDITARR_DATA_DIR}/updater/apply.request`` and writes
#      ``${AUDITARR_DATA_DIR}/updater/apply.status`` back.
#   3. Set AUDITARR_COMPOSE_FILE to your docker-compose.yml path.
#
# The sentinel payload looks like:
#
#   {"apply_id": "...", "from_version": "1.0.0", "to_version": "1.4.0",
#    "requested_at": "2026-..."}
#
# We write a status file shaped like:
#
#   {"apply_id": "...", "status": "completed", "detail": "pulled image"}
#
# That matches what UpdaterService.poll_apply_status() expects.

set -euo pipefail

: "${AUDITARR_DATA_DIR:?Set AUDITARR_DATA_DIR to the host path bound to /app/data}"
: "${AUDITARR_COMPOSE_FILE:?Set AUDITARR_COMPOSE_FILE to your compose file}"
POLL_INTERVAL="${AUDITARR_UPDATE_POLL_SECONDS:-5}"
SERVICE_NAME="${AUDITARR_COMPOSE_SERVICE:-app}"

UPDATER_DIR="${AUDITARR_DATA_DIR}/updater"
SENTINEL="${UPDATER_DIR}/apply.request"
STATUS="${UPDATER_DIR}/apply.status"

mkdir -p "$UPDATER_DIR"

log() {
    printf '[auditarr-update] %s %s\n' "$(date -Iseconds)" "$*"
}

write_status() {
    local apply_id="$1" status="$2" detail="$3" error="${4:-}"
    # Build the JSON without depending on jq — keep deps minimal.
    {
        printf '{'
        printf '"apply_id":"%s",' "$apply_id"
        printf '"status":"%s",' "$status"
        printf '"detail":%s' "$(printf '%s' "$detail" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')"
        if [[ -n "$error" ]]; then
            printf ',"error":%s' "$(printf '%s' "$error" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))')"
        fi
        printf '}'
    } > "$STATUS"
}

apply_update() {
    local apply_id="$1" to_version="$2"

    log "apply requested: id=$apply_id to=$to_version"
    write_status "$apply_id" "running" "pulling images"

    if ! docker compose -f "$AUDITARR_COMPOSE_FILE" pull "$SERVICE_NAME" 2>&1; then
        log "pull failed for $SERVICE_NAME"
        write_status "$apply_id" "failed" "docker compose pull failed" \
            "Check that the registry is reachable and the tag exists."
        return
    fi

    if ! docker compose -f "$AUDITARR_COMPOSE_FILE" up -d "$SERVICE_NAME" 2>&1; then
        log "up -d failed for $SERVICE_NAME"
        write_status "$apply_id" "failed" "docker compose up -d failed" \
            "Container did not start. Inspect with: docker compose logs $SERVICE_NAME"
        return
    fi

    log "apply complete: $to_version"
    write_status "$apply_id" "completed" \
        "Pulled $SERVICE_NAME image and recreated container."
}

log "watching $SENTINEL (interval=${POLL_INTERVAL}s)"
while true; do
    if [[ -f "$SENTINEL" ]]; then
        payload=$(<"$SENTINEL")
        apply_id=$(printf '%s' "$payload" \
            | python3 -c 'import json,sys;print(json.load(sys.stdin).get("apply_id",""))')
        to_version=$(printf '%s' "$payload" \
            | python3 -c 'import json,sys;print(json.load(sys.stdin).get("to_version",""))')

        if [[ -z "$apply_id" || -z "$to_version" ]]; then
            log "malformed sentinel; ignoring"
        else
            # Remove the request before doing the work so a slow
            # docker pull doesn't trigger a re-run on the next tick.
            rm -f "$SENTINEL"
            apply_update "$apply_id" "$to_version" || true
        fi
    fi
    sleep "$POLL_INTERVAL"
done
