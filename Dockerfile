# Stage 1 - frontend build

FROM node:26-alpine AS frontend-build
WORKDIR /build/frontend

COPY frontend/package.json frontend/package-lock.json* ./
RUN --mount=type=cache,target=/root/.npm \
    npm install --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

# Stage 2 - backend dependency layer (uv)
FROM ghcr.io/astral-sh/uv:0.9.30-python3.12-bookworm-slim AS backend-deps
WORKDIR /build/backend

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY backend/pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/venv --python 3.12 \
 && uv pip compile pyproject.toml --quiet -o requirements.txt \
 && uv pip install --python /opt/venv/bin/python \
        -r requirements.txt

COPY backend/ ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/venv/bin/python --no-deps .

# Stage 3 - runtime
FROM python:3.12-slim-bookworm AS runtime

ARG APP_USER=auditarr
ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    AUDITARR_FRONTEND_DIST=/app/frontend \
    AUDITARR_BUILTIN_PLUGIN_DIR=/app/builtin-plugins \
    AUDITARR_PLUGIN_DIR=/app/plugins \
    AUDITARR_HOST=0.0.0.0 \
    AUDITARR_PORT=8000

# ffprobe for media analysis (used in Stage 4+, but the binary is part of the
# runtime contract so production images are upgrade-safe).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        ca-certificates \
        tini \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid ${APP_GID} ${APP_USER} \
 && useradd --uid ${APP_UID} --gid ${APP_GID} --shell /usr/sbin/nologin --create-home ${APP_USER}

COPY --from=backend-deps /opt/venv /opt/venv
COPY --from=backend-deps /build/backend /app/backend
COPY --from=frontend-build /build/frontend/dist /app/frontend
COPY docs /app/docs
COPY docker/entrypoint.sh /usr/local/bin/auditarr-entrypoint
COPY scripts/healthcheck.sh /usr/local/bin/auditarr-healthcheck
RUN chmod +x /usr/local/bin/auditarr-entrypoint /usr/local/bin/auditarr-healthcheck \
 && mkdir -p /app/data /app/plugins \
 && cp -R /app/backend/plugins/. /app/builtin-plugins/ 2>/dev/null \
    || mkdir -p /app/builtin-plugins \
 && chown -R ${APP_USER}:${APP_USER} /app

WORKDIR /app/backend
USER ${APP_USER}:${APP_USER}

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["/usr/local/bin/auditarr-healthcheck"]

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/auditarr-entrypoint"]
CMD ["serve"]
