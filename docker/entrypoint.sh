#!/usr/bin/env sh
# Auditarr container entrypoint.
#
# Forms:
#   auditarr-entrypoint serve         — run migrations + start API (default)
#   auditarr-entrypoint cli ...       — exec the CLI with the remaining args
#   auditarr-entrypoint sh            — shell into the container
set -eu

cd /app/backend

case "${1:-serve}" in
  serve)
    if [ "${AUDITARR_RUN_MIGRATIONS:-1}" = "1" ]; then
      echo "[entrypoint] running database migrations"
      alembic upgrade head || {
        echo "[entrypoint] migrations failed; aborting" >&2
        exit 1
      }
    fi
    exec gunicorn \
      --bind "${AUDITARR_HOST:-0.0.0.0}:${AUDITARR_PORT:-8000}" \
      --workers "${AUDITARR_WORKERS:-2}" \
      --worker-class uvicorn.workers.UvicornWorker \
      --timeout "${AUDITARR_WORKER_TIMEOUT:-60}" \
      --graceful-timeout 30 \
      --access-logfile - \
      --error-logfile - \
      app.main:app
    ;;
  cli)
    shift
    exec auditarr "$@"
    ;;
  sh|bash)
    exec /bin/sh
    ;;
  *)
    exec auditarr "$@"
    ;;
esac
