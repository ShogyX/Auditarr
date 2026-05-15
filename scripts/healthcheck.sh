#!/usr/bin/env sh
# Lightweight liveness probe — hits /api/v1/health/live without external deps.
set -eu

PORT="${AUDITARR_PORT:-8000}"
URL="http://127.0.0.1:${PORT}/api/v1/health/live"

if command -v curl >/dev/null 2>&1; then
  exec curl --silent --fail --max-time 4 "$URL" >/dev/null
elif command -v wget >/dev/null 2>&1; then
  exec wget --quiet --tries=1 --timeout=4 --spider "$URL"
else
  echo "[healthcheck] neither curl nor wget available" >&2
  exit 1
fi
