"""``GET /api/v1/system/changelog`` regression tests (Stage 1 / L2).

The previous implementation imported ``from app.config import
get_settings`` — a module that has never existed in this codebase. The
``except Exception`` swallowed the ``ImportError`` silently, so the
bare-metal candidate was never contributed to the search path. The
fallback path then walked up looking for a directory containing
``pyproject.toml`` and stopped there — but in the project layout the
``pyproject.toml`` lives under ``backend/`` while the CHANGELOG lives
at the repository root one level higher, so the file was never found
and every authenticated caller hit a 404.

These tests pin:
  (1) the helper finds the project-root CHANGELOG in the dev layout
      where pyproject.toml is one level deep
  (2) ``GET /system/changelog`` returns 200 with non-empty body_html
      for an authenticated caller
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.settings import get_settings
from app.events.bus import get_event_bus
from app.main import create_app
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database


# ── _find_changelog() unit-style tests ───────────────────────────
def test_find_changelog_walks_one_level_above_pyproject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Given a layout where ``pyproject.toml`` is in ``project/backend``
    and ``CHANGELOG.md`` is in ``project/``, the helper must find it."""
    from app.api.v1 import system as system_mod

    project_root = tmp_path / "project"
    backend_dir = project_root / "backend"
    backend_dir.mkdir(parents=True)
    (backend_dir / "pyproject.toml").write_text("[project]\nname='x'\n")
    changelog = project_root / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\nv1.0.0 — initial.\n", encoding="utf-8")

    # Make the helper believe its source file lives inside the fake
    # project layout, so the walk has the expected shape.
    fake_self = backend_dir / "app" / "api" / "v1" / "system.py"
    fake_self.parent.mkdir(parents=True)
    fake_self.write_text("# stand-in for the real module")

    monkeypatch.setattr(
        system_mod, "__file__", str(fake_self), raising=True
    )
    # Point the bare-metal candidate somewhere that won't match, so we
    # exercise the layout-walk branch.
    monkeypatch.setenv("AUDITARR_DATA_DIR", str(tmp_path / "irrelevant"))
    get_settings.cache_clear()

    resolved = system_mod._find_changelog()
    assert resolved is not None
    assert resolved.resolve() == changelog.resolve()


def test_find_changelog_returns_none_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no CHANGELOG exists anywhere in the walked path, the helper
    returns ``None`` and the endpoint surfaces a 404."""
    from app.api.v1 import system as system_mod

    barren = tmp_path / "barren"
    barren.mkdir()
    fake_self = barren / "app" / "api" / "v1" / "system.py"
    fake_self.parent.mkdir(parents=True)
    fake_self.write_text("# stand-in")

    monkeypatch.setattr(
        system_mod, "__file__", str(fake_self), raising=True
    )
    monkeypatch.setenv("AUDITARR_DATA_DIR", str(tmp_path / "no-changelog"))
    get_settings.cache_clear()

    assert system_mod._find_changelog() is None


# ── Live endpoint test ───────────────────────────────────────────
@pytest_asyncio.fixture
async def auth_client() -> AsyncIterator[AsyncClient]:
    """Fresh in-memory SQLite app per test."""
    get_settings.cache_clear()
    db = get_database()
    redis = get_redis()
    bus = get_event_bus()
    bus.clear()

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as c:
            yield c
    finally:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        try:
            await redis.disconnect()
        except Exception:
            pass  # best-effort cleanup in test helper
        bus.clear()


PASSWORD = "supersecret-password-1!"


async def _register_and_login(client: AsyncClient) -> dict[str, str]:
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "user@example.com",
            "username": "viewer",
            "password": PASSWORD,
        },
    )
    assert r.status_code == 201, r.text
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "viewer", "password": PASSWORD},
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.asyncio
async def test_changelog_endpoint_requires_auth(
    auth_client: AsyncClient,
) -> None:
    """The endpoint takes ``CurrentUser`` — unauthenticated callers get 401."""
    r = await auth_client.get("/api/v1/system/changelog")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_changelog_endpoint_returns_200_for_authenticated_user(
    auth_client: AsyncClient,
) -> None:
    """The real test of L2: the project ships a CHANGELOG.md at the
    repo root, and the helper must locate it from anywhere under
    ``backend/``."""
    headers = await _register_and_login(auth_client)
    r = await auth_client.get("/api/v1/system/changelog", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "body_html" in body
    assert body["body_html"], "Rendered CHANGELOG body must not be empty"
    assert "body_markdown" in body
    assert body["body_markdown"], "Raw CHANGELOG markdown must not be empty"
