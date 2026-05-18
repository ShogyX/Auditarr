#!/usr/bin/env bash
# Auditarr update preflight (bare-metal install).
#
# v1.9.1 Stage 1.6 — verify the host is ready to run an in-UI Apply
# before the watcher starts touching anything. Print a tabular
# summary on stdout; exit 0 if every check passes, non-zero with the
# same summary if any check fails. The watcher invokes this first
# and captures the output as the failure detail surfaced in the UI;
# operators can also run it standalone to validate a host before
# clicking Apply:
#
#     sudo /opt/auditarr/updater/auditarr-update-preflight.sh
#
# Checks (in order):
#   1. Required binaries on PATH
#   2. systemctl unit access (auditarr-api, auditarr-worker)
#   3. Network reachability to the update feed
#   4. Release URL configured or derivable
#   5. Free disk space on the staging filesystem
#   6. APP_HOME + STATE_DIR writable
#   7. App user exists
#
# Exits 0 / 2.

set -uo pipefail

STATE_DIR="${AUDITARR_STATE_DIR:-/var/lib/auditarr}"
APP_HOME="${AUDITARR_HOME:-/opt/auditarr}"
APP_USER="${AUDITARR_USER:-auditarr}"
UPDATE_FEED_URL="${AUDITARR_UPDATE_FEED_URL:-}"
RELEASE_URL_TEMPLATE="${AUDITARR_RELEASE_TARBALL_URL:-}"
MIN_FREE_MB="${AUDITARR_APPLY_MIN_FREE_MB:-1024}"

STAGING_DIR="${STATE_DIR}/updater/staging"
mkdir -p "$STAGING_DIR" 2>/dev/null || true

results=()       # human-readable per-check status lines
fail_count=0

record() {
    local outcome="$1" name="$2" detail="$3"
    results+=("$(printf '  [%s] %-28s %s' "$outcome" "$name" "$detail")")
    if [[ "$outcome" != "ok" ]]; then
        fail_count=$((fail_count + 1))
    fi
}

# ── 1. Required binaries ─────────────────────────────────────────
missing_bins=()
for bin in curl tar systemctl install rsync sudo python3 chmod timeout sha256sum awk df id; do
    if ! command -v "$bin" >/dev/null 2>&1; then
        missing_bins+=("$bin")
    fi
done
if (( ${#missing_bins[@]} == 0 )); then
    record "ok" "binaries" "all required binaries on PATH"
else
    record "FAIL" "binaries" "missing: ${missing_bins[*]}"
fi

# ── 2. systemctl unit access ─────────────────────────────────────
unit_problems=()
for unit in auditarr-api auditarr-worker; do
    if ! systemctl --quiet is-enabled "$unit" 2>/dev/null; then
        unit_problems+=("$unit")
    fi
done
if (( ${#unit_problems[@]} == 0 )); then
    record "ok" "systemd units" "auditarr-api + auditarr-worker enabled"
else
    record "FAIL" "systemd units" "not enabled or unknown: ${unit_problems[*]}"
fi

# ── 3. Network reachability ──────────────────────────────────────
if [[ -z "$UPDATE_FEED_URL" ]]; then
    record "FAIL" "feed reachable" "AUDITARR_UPDATE_FEED_URL is empty"
elif curl --max-time 10 --connect-timeout 5 -sf -o /dev/null "$UPDATE_FEED_URL"; then
    record "ok" "feed reachable" "$UPDATE_FEED_URL"
else
    record "FAIL" "feed reachable" "cannot fetch $UPDATE_FEED_URL"
fi

# ── 4. Release URL configured or derivable ───────────────────────
if [[ -n "$RELEASE_URL_TEMPLATE" ]]; then
    record "ok" "release URL" "explicit (AUDITARR_RELEASE_TARBALL_URL)"
elif [[ "$UPDATE_FEED_URL" =~ ^https://api\.github\.com/repos/[^/]+/[^/]+/releases/latest$ ]]; then
    record "ok" "release URL" "will derive from GitHub feed URL"
else
    record "FAIL" "release URL" \
        "set AUDITARR_RELEASE_TARBALL_URL or point AUDITARR_UPDATE_FEED_URL at api.github.com/repos/.../releases/latest"
fi

# ── 5. Free disk space ───────────────────────────────────────────
if [[ -d "$STAGING_DIR" ]]; then
    avail_kb="$(df -P "$STAGING_DIR" 2>/dev/null | awk 'NR==2 {print $4}')"
    avail_mb=$(( ${avail_kb:-0} / 1024 ))
    if (( avail_mb >= MIN_FREE_MB )); then
        record "ok" "disk space" "${avail_mb}MB free on staging fs (>= ${MIN_FREE_MB}MB)"
    else
        record "FAIL" "disk space" "${avail_mb}MB free on staging fs (need ${MIN_FREE_MB}MB)"
    fi
else
    record "FAIL" "disk space" "staging dir $STAGING_DIR missing"
fi

# ── 6. Writable paths ────────────────────────────────────────────
if [[ -w "$APP_HOME" ]]; then
    record "ok" "APP_HOME writable" "$APP_HOME"
else
    record "FAIL" "APP_HOME writable" "$APP_HOME not writable (run as root?)"
fi
if [[ -w "$STATE_DIR" ]]; then
    record "ok" "STATE_DIR writable" "$STATE_DIR"
else
    record "FAIL" "STATE_DIR writable" "$STATE_DIR not writable"
fi

# ── 7. App user ──────────────────────────────────────────────────
if id -u "$APP_USER" >/dev/null 2>&1; then
    record "ok" "app user" "$APP_USER exists"
else
    record "FAIL" "app user" "user '$APP_USER' does not exist"
fi

# ── Summary ──────────────────────────────────────────────────────
printf 'Auditarr update preflight (%s):\n' "$(date -Iseconds)"
for line in "${results[@]}"; do
    printf '%s\n' "$line"
done

if (( fail_count == 0 )); then
    printf '\nResult: PASS — host is ready for an Auditarr update.\n'
    exit 0
fi

printf '\nResult: FAIL — %d check(s) failed. Fix the above and re-run.\n' "$fail_count"
exit 2
