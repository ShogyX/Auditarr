# Auditarr — Documentation

This file is the canonical home of Auditarr's user-facing documentation. Stage 3
implements the Markdown rendering engine, search index, and contextual help
system that surfaces this content inside the application.

## Table of contents (planned)

- Architecture overview
- API reference (auto-generated from OpenAPI)
- Feature documentation
  - Media core
  - Rules engine — examples
  - Automation engine — examples
  - Notifications
  - Optimization
- Integration documentation (Plex, Jellyfin, Sonarr, Radarr, Bazarr, Tdarr)
- Plugin development guide
- Troubleshooting
- Install instructions
- Update instructions

## Stage 1 status

The foundation ships:

- a versioned API (`/api/v1`)
- a plugin loader with manifest validation and dependency-ordered loading
- an event bus with 24 canonical event names
- a service registry with capability lookup
- a frontend shell visually equivalent to the existing Auditarr UI
- a Docker stack (app + Postgres + Redis) with healthchecks
- CI gates on lint, typecheck, tests, and Docker build

Subsequent stages add concrete features without modifying these contracts.
