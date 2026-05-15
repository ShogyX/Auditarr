---
id: guide/architecture
title: Architecture
category: guide
tags: [architecture, internals]
summary: Modular monolith with event bus, plugin SDK, and contract-isolated modules.
help_context: [help.architecture]
related: [overview, reference/plugins]
---

# Architecture

Auditarr is a **modular monolith**: one process, multiple modules with hard
contracts between them.

## Cross-module communication

Modules communicate through three contracts only:

1. **Event bus** — every important action emits a normalized domain event
   (`scan.completed`, `rule.triggered`, …). Subscribers react asynchronously.
2. **Service registry** — singletons accessed by capability name, not by
   feature module.
3. **Plugin SDK** — a typed `PluginContext` surface that exposes the router,
   capability registration, event subscription, and a logger. Nothing else.

There are no direct cross-module imports. This is enforced by code review;
the cost is small and the payoff (predictable refactors, clean plugin
isolation) is large.

## API versioning

The HTTP surface is versioned at `/api/v1/`. Breaking changes require
`/api/v2/`. The version segment is configurable but defaults are stable.

## Database

PostgreSQL is the production target. SQLite is supported for testing only.
All schema changes flow through Alembic migrations.

## Frontend

A Vite + React + TypeScript SPA, built into static assets and served by the
backend in production. Plugins can register additional pages, sidebar
entries, widgets, and settings sections through the frontend plugin
registry.
