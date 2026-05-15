#!/usr/bin/env bash
# scripts/post-fix-smoke.sh
#
# Stage 16 (audit follow-up): smoke-test the audit fixes from a
# live-API perspective. Hits a handful of endpoints touched by the
# 16-stage fix plan and asserts each returns HTTP 200.
#
# Usage:
#   AUDITARR_BASE=http://localhost:8000 \
#   AUDITARR_TOKEN=<bearer-from-auth-login> \
#   ./scripts/post-fix-smoke.sh
#
# Exit codes:
#   0 — every endpoint returned 200 AND the notifications/kinds
#       response contains both ``email`` and ``webhook``.
#   1 — at least one check failed.
#
# Bring-your-own-bootstrap. This script does not register a user
# or log one in; it expects an existing admin bearer token in
# AUDITARR_TOKEN (admin role is required for /audit/log and for
# /system/housekeeping/last-run — both Stage 14 additions).

set -u

BASE="${AUDITARR_BASE:-http://localhost:8000}"
TOKEN="${AUDITARR_TOKEN:-}"

if [[ -z "$TOKEN" ]]; then
    echo "ERROR: AUDITARR_TOKEN environment variable not set." >&2
    echo "Obtain one via POST /api/v1/auth/login and pass it in." >&2
    exit 1
fi

declare -i FAIL=0
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

# check <path> [--require <jq-expr>]
# Calls GET $BASE/api/v1$path with bearer auth. Asserts HTTP 200.
# Optional --require runs a jq expression against the response body
# and fails the check unless the result is truthy.
check() {
    local path="$1"
    shift
    local require=""
    if [[ "${1:-}" == "--require" ]]; then
        require="$2"
        shift 2
    fi

    local body="$TMPDIR/body.json"
    local code
    code=$(curl -sS -o "$body" -w '%{http_code}' \
        -H "authorization: Bearer ${TOKEN}" \
        "${BASE}/api/v1${path}" || echo "000")

    if [[ "$code" != "200" ]]; then
        echo "FAIL  ${path} → HTTP ${code}"
        cat "$body" 2>/dev/null | head -c 200 >&2
        echo >&2
        FAIL=$((FAIL + 1))
        return
    fi

    if [[ -n "$require" ]]; then
        if command -v jq >/dev/null 2>&1; then
            if ! jq -e "$require" "$body" >/dev/null 2>&1; then
                echo "FAIL  ${path} → 200 but assertion failed: ${require}"
                FAIL=$((FAIL + 1))
                return
            fi
        else
            echo "WARN  ${path} → 200 (jq not installed; skipping assertion)"
            return
        fi
    fi

    echo "OK    ${path}"
}

echo "== Stage 16 post-fix smoke against ${BASE} =="

# Stage 5 (audit follow-up): /auth/me is the canonical bootstrap probe.
check "/auth/me"

# Stage 12 audit fix (Issue 17): /system/changelog returns rendered
# markdown for the in-app Changelog page.
check "/system/changelog"

# Dashboard rollup — touched in stages 7, 11+expand, 26, 30, 31, 14.1.
check "/dashboard/overview"

# Stage 23: media list.
check "/media?limit=1"

# Stage 12 (audit follow-up): playback stats by device.
check "/playback/stats/devices?days=1"

# Stage 14 (audit follow-up): audit log viewer endpoint (admin only).
check "/audit/log?limit=1"

# Stage 15 (audit follow-up): both email and webhook providers must
# appear in the kinds list — the test is non-trivial because Stage
# 15 was the only stage that added runtime behaviour without a user
# report driving it.
check "/notifications/kinds" --require '[.[].kind] | (contains(["email"]) and contains(["webhook"]))'

echo
if (( FAIL == 0 )); then
    echo "PASS  all checks succeeded"
    exit 0
else
    echo "FAIL  ${FAIL} check(s) failed"
    exit 1
fi
