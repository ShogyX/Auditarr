---
id: guide/quickstart
title: Quick start
category: guide
tags: [getting-started, install]
summary: First-boot installation and admin setup with Docker Compose.
help_context: [help.install, settings.admin]
related: [overview, guide/architecture]
---

# Quick start

## Prerequisites

- Docker 24+ with Docker Compose v2
- ~1 GB disk for the container image and PostgreSQL data

## Install

```bash
git clone https://github.com/example/auditarr.git
cd auditarr
cp .env.example .env
```

Edit `.env` and at minimum set:

```bash
AUDITARR_SECRET_KEY=<run python -c "import secrets; print(secrets.token_urlsafe(64))">
POSTGRES_PASSWORD=<a strong database password>
AUDITARR_BOOTSTRAP_ADMIN_USERNAME=admin
AUDITARR_BOOTSTRAP_ADMIN_PASSWORD=at-least-twelve-characters
AUDITARR_BOOTSTRAP_ADMIN_EMAIL=admin@example.com
```

Then bring it up:

```bash
docker compose up -d
```

Open `http://localhost:8000` and sign in with the admin credentials you set.

## What happens on first boot

- Migrations run automatically.
- Docs are loaded from `/app/docs/`.
- Plugins are scanned (built-in plugins from `/app/builtin-plugins`, user
  plugins from `/app/plugins`).
- If `AUDITARR_BOOTSTRAP_ADMIN_USERNAME` is set and no users exist yet, an
  admin account is created.
