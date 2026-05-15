# Auditarr — Backend

FastAPI + SQLAlchemy 2 (async) + Alembic + uv. See the repository root
[`README.md`](../README.md) for the full project overview.

## Local development

Prerequisites: Python 3.12+, [uv](https://docs.astral.sh/uv/), PostgreSQL 16,
Redis 7.

```bash
uv sync --extra dev
cp .env.example .env
uv run alembic upgrade head
uv run auditarr serve --reload
```

The API listens on `http://localhost:8000` with the Swagger UI at
`/api/v1/swagger` and the documentation engine at `/api/v1/docs/`.

## CLI

```bash
uv run auditarr --help          # list commands
uv run auditarr db-check        # verify the database connection
uv run auditarr redis-check     # verify Redis
uv run auditarr plugin-list     # discover plugins on disk
uv run auditarr serve --reload  # run the API with auto-reload
```

## Tests + quality gates

```bash
uv run pytest -q                # unit + integration
uv run ruff check .             # lint
uv run ruff format --check .    # format
uv run mypy app                 # types
```

## Migrations

```bash
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
uv run alembic downgrade -1
```

Models added under `app/models/` must be imported from `app/models/__init__.py`
so Alembic autogenerate sees them.

## Layout

```
app/
  api/         routers, middleware, error handlers, websocket
  core/        settings, logging, registry, exceptions
  events/      domain event bus + canonical event names
  plugins/     manifest schema, contracts, loader
  storage/     async DB engine, Redis client, declarative base
  services/    business-logic services (per-stage)
  models/      ORM models
  schemas/     shared response schemas
  cli.py       Typer CLI
  main.py      FastAPI app factory + lifespan
migrations/    alembic
plugins/       on-disk plugins (example-hello included)
tests/         pytest unit + integration
```

## Architectural rules

- All cross-module communication goes through the **event bus**, **service
  registry**, or **plugin SDK** — never via direct imports between feature
  modules.
- All schema changes require an Alembic migration. No runtime schema mutation.
- API surface is versioned at `/api/v1/`. Breaking changes require `/api/v2/`.
- Plugins cannot import repositories, sessions, or core middleware. They
  receive a typed `PluginContext` and use only that.

These contracts are frozen after each completed stage.
