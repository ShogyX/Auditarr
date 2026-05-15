"""Auditarr CLI."""

from __future__ import annotations

import asyncio
import json
import sys

import typer
import uvicorn

from app import __version__
from app.core.logging import configure_logging, get_logger
from app.core.settings import get_settings

cli = typer.Typer(
    name="auditarr",
    help="Auditarr operational CLI.",
    no_args_is_help=True,
    add_completion=False,
)


@cli.command()
def version() -> None:
    """Print the application version."""
    typer.echo(__version__)


@cli.command()
def serve(
    host: str | None = typer.Option(None, help="Override AUDITARR_HOST"),
    port: int | None = typer.Option(None, help="Override AUDITARR_PORT"),
    reload: bool = typer.Option(False, help="Auto-reload on code changes"),
    workers: int = typer.Option(1, help="Number of uvicorn workers"),
) -> None:
    """Run the API server."""
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=host or settings.host,
        port=port or settings.port,
        reload=reload,
        workers=workers if not reload else 1,
        log_config=None,
    )


@cli.command("db-check")
def db_check() -> None:
    """Verify the database connection."""

    async def _run() -> bool:
        from app.storage.database import get_database

        db = get_database()
        await db.connect()
        try:
            return await db.healthcheck()
        finally:
            await db.disconnect()

    ok = asyncio.run(_run())
    typer.echo("database: ok" if ok else "database: FAILED")
    sys.exit(0 if ok else 1)


@cli.command("redis-check")
def redis_check() -> None:
    """Verify the Redis connection."""

    async def _run() -> bool:
        from app.storage.cache import get_redis

        client = get_redis()
        await client.connect()
        try:
            return await client.healthcheck()
        finally:
            await client.disconnect()

    ok = asyncio.run(_run())
    typer.echo("redis: ok" if ok else "redis: FAILED")
    sys.exit(0 if ok else 1)


# ── Stage 18: user-management commands ───────────────────────
# Used by install-bare-metal.sh to bootstrap the first admin and to
# detect whether an existing install already has one. Also useful
# for ops humans running the CLI directly.

user_cli = typer.Typer(
    name="user",
    help="User management commands.",
    no_args_is_help=True,
)
cli.add_typer(user_cli, name="user")


@user_cli.command("count-admins")
def user_count_admins() -> None:
    """Print the number of admin users to stdout.

    Bare-metal installer uses this to decide whether to prompt for
    first-admin credentials. Returns 0 cleanly even when there are
    zero admins, so callers can read stdout without worrying about
    exit-code/stderr juggling.

    Library log lines are routed to stderr via ``configure_logging``
    so callers can ``$(...)``-capture the count without trailing
    structlog noise.
    """
    configure_logging(get_settings())

    async def _run() -> int:
        from sqlalchemy import func, select

        from app.models.user import User
        from app.storage.database import get_database

        db = get_database()
        await db.connect()
        try:
            async with db.session() as session:
                stmt = select(func.count()).select_from(User).where(
                    User.role == "admin"
                )
                result = await session.execute(stmt)
                return int(result.scalar_one())
        finally:
            await db.disconnect()

    count = asyncio.run(_run())
    typer.echo(str(count))


@user_cli.command("bootstrap-admin")
def user_bootstrap_admin(
    email: str = typer.Option(..., "--email", help="Admin email address"),
    username: str = typer.Option(..., "--username", help="Admin username"),
    password_from_env: str = typer.Option(
        ...,
        "--password-from-env",
        help=(
            "Environment variable name holding the password. "
            "We never accept a password on the command line so it "
            "doesn't end up in /proc/<pid>/cmdline or shell history."
        ),
    ),
) -> None:
    """Create the first admin user.

    Refuses to run if a user with the given email or username already
    exists. Designed to be safe to re-run in idempotent installer
    flows: the caller should check ``count-admins`` first and skip
    this command if the count is > 0.
    """
    import os

    configure_logging(get_settings())

    plaintext = os.environ.get(password_from_env, "")
    if len(plaintext) < 12:
        typer.echo(
            f"error: ${password_from_env} must be set and at least 12 chars",
            err=True,
        )
        sys.exit(2)

    async def _run() -> tuple[int, str]:
        from app.models.user import User
        from app.security.passwords import hash_password
        from app.services.repositories.user import UserRepository
        from app.storage.database import get_database

        db = get_database()
        await db.connect()
        try:
            async with db.session() as session:
                repo = UserRepository(session)
                if await repo.get_by_email(email):
                    return (3, f"user with email {email} already exists")
                if await repo.get_by_username(username):
                    return (3, f"username {username} already taken")
                user = User(
                    email=email.lower(),
                    username=username.lower(),
                    password_hash=hash_password(plaintext),
                    role="admin",
                    is_active=True,
                )
                await repo.add(user)
                await session.commit()
                return (0, f"admin user {username} created")
        finally:
            await db.disconnect()

    code, message = asyncio.run(_run())
    if code == 0:
        typer.echo(message)
    else:
        typer.echo(f"error: {message}", err=True)
    sys.exit(code)


@cli.command("plugin-list")
def plugin_list() -> None:
    """Discover plugins on disk and print their manifests."""
    settings = get_settings()
    configure_logging(settings)
    log = get_logger("auditarr.cli", category="system")

    async def _run() -> list[dict[str, object]]:
        from app.plugins.loader import get_plugin_loader

        loader = get_plugin_loader()
        await loader.discover_and_load(app=None)
        return loader.list_summary()

    summary = asyncio.run(_run())
    log.info("cli.plugin_list", count=len(summary))
    typer.echo(json.dumps(summary, indent=2))


@cli.command("plugin-new")
def plugin_new(
    slug: str = typer.Argument(
        ...,
        help=(
            "Lowercase plugin id: starts with a letter, alphanumeric + dashes, "
            "2–48 chars. Becomes the manifest id and the directory name."
        ),
    ),
    target_dir: str = typer.Option(
        "./plugins",
        "--target-dir",
        "-t",
        help="Parent directory to create the plugin folder under.",
    ),
    description: str = typer.Option(
        "TODO: short description.",
        "--description",
        "-d",
        help="Initial description in the generated manifest.",
    ),
) -> None:
    """Scaffold a new plugin with a working skeleton.

    Produces a directory containing:

      - ``manifest.json``       — plugin metadata
      - ``__init__.py``         — register(context) entry point with the
                                  on_startup/on_shutdown lifecycle wired up
      - ``README.md``           — quickstart for the plugin author
      - ``tests/test_plugin.py``— starter pytest suite

    The skeleton declares a tiny Pydantic ``settings_schema`` so the
    plugin shows up in the Plugins → Settings UI immediately.
    """
    import re
    from pathlib import Path

    slug_re = re.compile(r"^[a-z][a-z0-9-]{1,47}$")
    if not slug_re.match(slug):
        typer.echo(
            "Invalid slug. Use lowercase letters/digits/dashes, "
            "starting with a letter, 2–48 chars.",
            err=True,
        )
        raise typer.Exit(code=2)

    root = Path(target_dir).expanduser().resolve() / slug
    if root.exists():
        typer.echo(f"Target {root} already exists.", err=True)
        raise typer.Exit(code=2)

    pkg_name = slug.replace("-", "_")
    title = slug.replace("-", " ").title()

    root.mkdir(parents=True)
    (root / "tests").mkdir()

    (root / "manifest.json").write_text(
        json.dumps(
            {
                "id": slug,
                "name": title,
                "version": "0.1.0",
                "type": "generic",
                "description": description,
                "backend_entry": "__init__.py",
                "capabilities": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    (root / "__init__.py").write_text(
        f'''"""Auditarr plugin: {title}.

Generated by ``auditarr plugin-new``. Edit freely.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.plugins.contracts import Plugin, PluginContext


class {pkg_name.title().replace("_", "")}Settings(BaseModel):
    """Operator-editable config exposed in the Plugins UI."""

    enabled: bool = Field(default=True)
    # TODO: replace with your real settings.
    message: str = Field(default="hello world")


class {pkg_name.title().replace("_", "")}Plugin(Plugin):
    settings_schema = {pkg_name.title().replace("_", "")}Settings

    async def on_startup(self) -> None:
        """Called after the host is fully up.

        Spawn long-running tasks here. The host won't block on this
        method; an exception is logged + isolated.
        """
        log = self.context.logger()
        log.info("{slug}.started")

    async def on_shutdown(self) -> None:
        """Called during graceful shutdown, before on_unload."""
        log = self.context.logger()
        log.info("{slug}.stopping")


async def register(context: PluginContext) -> {pkg_name.title().replace("_", "")}Plugin:
    """Entry point the loader calls. Wire up your capabilities here."""
    # Example: register a notification channel or integration here via
    # ``context.register_integration(...)`` /
    # ``context.register_notification_channel(...)``.
    return {pkg_name.title().replace("_", "")}Plugin(context)
''',
        encoding="utf-8",
    )

    (root / "README.md").write_text(
        f"""# {title}

Auditarr plugin scaffolded with `auditarr plugin-new`.

## What's in here

- `manifest.json` — declares the plugin id (`{slug}`), version, type.
- `__init__.py` — `register()` returns a `Plugin` instance. Add your
  capabilities (integration, notification channel, etc.) inside the
  body of `register`.
- `tests/test_plugin.py` — minimal pytest suite verifying `register()`
  returns the plugin without raising.

## Running it

Drop this directory into your Auditarr plugin volume (usually
`./plugins/` next to your `docker-compose.yml`) and restart the
container. The plugin will appear in the **Plugins** page.

## Settings

The starter plugin exposes a `settings_schema`. Operators see the form
under **Plugins → {title} → Settings**. Read the persisted values from
your code with:

```python
from app.services.plugin_settings import PluginSettingsService

values = await PluginSettingsService(session).values_or_defaults("{slug}")
```

## Lifecycle hooks

- `on_load` — lightweight setup, runs synchronously during host startup.
- `on_startup` — long-running setup, runs as a background task after
  all plugins have loaded.
- `on_shutdown` — cancel background tasks here.
- `on_unload` — final cleanup.

A failing hook is logged + isolated; it cannot crash the host.

## Reference

See `docs/plugins/authoring.md` in the Auditarr distribution for the
full SDK reference.
""",
        encoding="utf-8",
    )

    (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_plugin.py").write_text(
        f'''"""Starter tests for the {slug} plugin."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _load_plugin_module():
    """Load ``__init__.py`` from the plugin directory by path.

    Mirrors how Auditarr's plugin loader imports the entry point — it
    doesn't require the plugin to be importable as a Python package on
    sys.path, so tests don't have to mess with PYTHONPATH either.
    """
    entry = Path(__file__).resolve().parent.parent / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "auditarr_plugin_under_test", entry
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_register_returns_plugin() -> None:
    """register() must return a Plugin instance the host can wire up."""
    from app.plugins.contracts import Plugin, PluginContext, PluginManifest, PluginType

    module = _load_plugin_module()

    manifest = PluginManifest(
        id="{slug}",
        name="{title}",
        version="0.1.0",
        type=PluginType.GENERIC,
        backend_entry="__init__.py",
    )
    context = PluginContext(
        manifest=manifest,
        directory=Path(__file__).resolve().parent.parent,
        registry=MagicMock(),
        event_bus=MagicMock(),
    )
    plugin = await module.register(context)
    assert isinstance(plugin, Plugin)
''',
        encoding="utf-8",
    )

    typer.echo(f"Plugin skeleton created at {root}")
    typer.echo("Next steps:")
    typer.echo(f"  1. cd {root}")
    typer.echo("  2. Edit __init__.py to add capabilities (integration, channel, etc.)")
    typer.echo("  3. Restart Auditarr and check the Plugins page")


@cli.command()
def worker() -> None:
    """Start the ARQ background worker (consumes scan jobs from Redis)."""
    # ``arq`` ships its own runner that wants to introspect ``WorkerSettings``.
    # We exec it here so the entrypoint is uniform with ``serve``.
    from arq.worker import run_worker

    from app.worker import WorkerSettings

    run_worker(WorkerSettings)  # type: ignore[arg-type]


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
