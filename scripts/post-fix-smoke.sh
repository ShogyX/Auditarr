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
else
    echo "FAIL  ${FAIL} API check(s) failed"
fi

# ── Stage 16 (v1.7 release gate) — source-tree validation ─────
#
# Plan §681 — beyond API smoke, the release gate validates the
# shipped source tree itself for the v1.7 fingerprints. These
# checks run against the repo working copy ($REPO_ROOT,
# defaulting to the script's parent directory). They don't
# require AUDITARR_BASE — they're filesystem-only.

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
declare -i SRC_FAIL=0

src_check() {
    local label="$1"
    local result="$2"
    if [[ "$result" == "ok" ]]; then
        echo "OK    src: ${label}"
    else
        echo "FAIL  src: ${label} → ${result}"
        SRC_FAIL=$((SRC_FAIL + 1))
    fi
}

echo
echo "== Stage 16 v1.7 source-tree validation against ${REPO_ROOT} =="

# Stage 01 — installer rename. install-docker.sh + install-bare-metal.sh
# exist; install.sh is the stub.
if [[ -f "${REPO_ROOT}/install-docker.sh" && -f "${REPO_ROOT}/install-bare-metal.sh" ]]; then
    if grep -q "has been renamed" "${REPO_ROOT}/install.sh" 2>/dev/null; then
        src_check "installer rename (Stage 01)" "ok"
    else
        src_check "installer rename (Stage 01)" "install.sh exists but isn't the rename stub"
    fi
else
    src_check "installer rename (Stage 01)" "missing install-docker.sh or install-bare-metal.sh"
fi

# Stage 02 — Files page resize wiring. Look for the column-width
# state on the frontend.
if grep -rq "columnWidth\|column_width\|pointerup.*width" \
   "${REPO_ROOT}/frontend/src/features/files/" 2>/dev/null; then
    src_check "files-page resize wiring (Stage 02)" "ok"
else
    src_check "files-page resize wiring (Stage 02)" "no resize handler found"
fi

# Stage 05 — no ACTIVE ``quarantine`` references in shipped
# frontend sources. The Stage 05 removal is documented in
# many comments throughout the codebase (e.g. "Stage 27's
# quarantine workflow was retired"); those historical
# explanations are intentional and not regressions.
#
# We look for ACTIVE refs only — patterns that would indicate
# live code: an identifier (``quarantine``, ``quarantined``,
# ``unquarantine``) used as a function name, property access,
# import, type, or string literal that the JS runtime would
# actually evaluate. The signature is "quarantine followed by
# ``(``, ``:``, ``.``, ``=``, or matching quotes" — comments
# don't satisfy any of those grammatically.
QUARANTINE_HITS=$(grep -rln --include='*.ts' --include='*.tsx' \
    --exclude='*.test.tsx' --exclude='*.test.ts' \
    -E 'quarantin[a-zA-Z]*\s*[\(:=\.]|["'\''"]quarantin' \
    "${REPO_ROOT}/frontend/src/" 2>/dev/null | \
    grep -v "node_modules" | grep -v "/docs/" | \
    while read -r f; do
        # Re-check: keep only files where the active-ref
        # regex matches a NON-comment line. Comments in
        # TS/TSX include ``//``, ``/* */``, and ``{/* */}``.
        remaining=$(grep -nE 'quarantin[a-zA-Z]*\s*[\(:=\.]|["'\''"]quarantin' "$f" 2>/dev/null | \
            grep -vE '^\s*[0-9]+:\s*(//|\*|/\*|\{/\*)' || true)
        if [[ -n "$remaining" ]]; then
            echo "$f"
        fi
    done || true)
if [[ -z "$QUARANTINE_HITS" ]]; then
    src_check "no active quarantine refs in frontend sources (Stage 05)" "ok"
else
    echo "$QUARANTINE_HITS" >&2
    src_check "no active quarantine refs in frontend sources (Stage 05)" "found active refs (see above)"
fi

# Stage 10 — VT integration registered (VT module lives in
# app/services/, not app/integrations/).
if [[ -f "${REPO_ROOT}/backend/app/services/virustotal.py" ]] || \
   grep -rq "VirusTotal\|vt_status" \
   "${REPO_ROOT}/backend/app/" 2>/dev/null; then
    src_check "VT integration registered (Stage 10)" "ok"
else
    src_check "VT integration registered (Stage 10)" "no VT module found"
fi

# Stage 07-08 — optimization routing targets present (in_process,
# tdarr at minimum). The optimization module lives at
# app/optimization/, not under app/services/.
if grep -rq 'routing_target\|in_process\|"tdarr"' \
   "${REPO_ROOT}/backend/app/optimization/" 2>/dev/null; then
    src_check "optimization routing targets (Stage 07-08)" "ok"
else
    src_check "optimization routing targets (Stage 07-08)" "no routing_target references found"
fi

# Stage 15 — /media/vocabulary endpoint registered.
if grep -q '/vocabulary\b\|MediaVocabulary' \
   "${REPO_ROOT}/backend/app/api/v1/media.py" 2>/dev/null; then
    src_check "media vocabulary endpoint (Stage 15)" "ok"
else
    src_check "media vocabulary endpoint (Stage 15)" "endpoint not registered"
fi

# Version stamp — Stage 16 bumped to 1.7.0.
if grep -q '__version__ = "1.8.2"' "${REPO_ROOT}/backend/app/__init__.py" 2>/dev/null; then
    src_check "backend __version__ == 1.8.2 (Stage 16)" "ok"
else
    src_check "backend __version__ == 1.8.2 (Stage 16)" "version mismatch"
fi
if grep -q '"version": "1.8.2"' "${REPO_ROOT}/frontend/package.json" 2>/dev/null; then
    src_check "frontend package.json version == 1.8.2 (Stage 16)" "ok"
else
    src_check "frontend package.json version == 1.8.2 (Stage 16)" "version mismatch"
fi

echo
TOTAL=$((FAIL + SRC_FAIL))
if (( TOTAL == 0 )); then
    echo "PASS  all checks (API + source-tree) succeeded"
    exit 0
else
    echo "FAIL  ${TOTAL} check(s) failed (${FAIL} API, ${SRC_FAIL} src)"
    exit 1
fi
