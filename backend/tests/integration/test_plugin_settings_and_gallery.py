"""Plugin settings and gallery API tests."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.core.registry import get_registry
from app.events.bus import get_event_bus
from app.main import create_app
from app.models.user import User
from app.plugins.contracts import Plugin, PluginContext, PluginManifest, PluginType
from app.plugins.loader import LoadedPlugin
from app.plugins.loader import get_plugin_loader
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


# A tiny in-memory test plugin we register directly with the loader
# rather than write to disk. It has a settings_schema so we can exercise
# the validation path.
class __FakePluginSettings:
    """Stand-in for a Pydantic settings model.

    We use the real BaseModel below — this is just here for the docstring.
    """


from pydantic import BaseModel, Field  # noqa: E402


class _FakeSettings(BaseModel):
    enabled: bool = True
    threshold: int = Field(default=10, ge=0, le=100)


class _FakePlugin(Plugin):
    settings_schema = _FakeSettings


def _inject_test_plugin() -> None:
    """Register a fake plugin directly into the loader's ``_plugins`` map.

    The real loader imports plugins from disk; for these tests we don't
    care about that surface, only about the settings/gallery endpoints
    that read from the loader's state.
    """
    loader = get_plugin_loader()
    manifest = PluginManifest(
        id="testplug",
        name="Test plugin",
        version="0.1.0",
        type=PluginType.GENERIC,
        backend_entry="__init__.py",
    )
    context = PluginContext(
        manifest=manifest,
        directory=Path("/tmp"),
        registry=get_registry(),
        event_bus=get_event_bus(),
    )
    loader._plugins[manifest.id] = LoadedPlugin(  # noqa: SLF001
        manifest=manifest, context=context, instance=_FakePlugin(context)
    )


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "plugins.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    monkeypatch.setenv(
        "AUDITARR_PLUGIN_GALLERY_URL", "https://gallery.test/manifest.json"
    )

    from app.core.settings import get_settings

    get_settings.cache_clear()

    # MockTransport for gallery fetches only; ASGITransport must still
    # work for API calls. Same fix as in Stage 11's updater tests.
    gallery_state: dict[str, Any] = {
        "body": {
            "plugins": [
                {
                    "id": "fingerprint",
                    "name": "Audio fingerprinting",
                    "description": "Detect duplicate tracks",
                    "version": "0.3.0",
                    "source_url": "https://github.com/example/fp",
                    "categories": ["analysis"],
                },
                {
                    "id": "testplug",  # matches our injected plugin
                    "name": "Test plugin",
                    "version": "0.1.0",
                },
                # Malformed entry — should be silently skipped.
                {"id": "", "name": ""},
            ]
        },
        "status": 200,
        "exc": None,
    }
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        if "transport" not in kwargs:

            def handler(request: httpx.Request) -> httpx.Response:
                if gallery_state["exc"] is not None:
                    raise gallery_state["exc"]
                return httpx.Response(
                    gallery_state["status"], json=gallery_state["body"]
                )

            kwargs["transport"] = httpx.MockTransport(handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001
    bus = get_event_bus()
    bus.clear()
    registry = get_registry()
    registry.clear()

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # Reset the plugin loader and inject our test plugin.
    loader = get_plugin_loader()
    loader._plugins.clear()  # noqa: SLF001
    _inject_test_plugin()

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            c._gallery_state = gallery_state  # type: ignore[attr-defined]
            yield c
    finally:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        try:
            await get_redis().disconnect()
        except Exception:  # noqa: BLE001
            pass
        bus.clear()
        registry.clear()
        loader._plugins.clear()  # noqa: SLF001
        get_settings.cache_clear()


async def _admin_headers(client: AsyncClient) -> dict[str, str]:
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "a@x.com", "username": "admin", "password": PASSWORD},
    )
    user_id = r.json()["id"]
    async with get_database().session() as sess:
        await sess.execute(
            update(User).where(User.id == user_id).values(role="admin")
        )
        await sess.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "admin", "password": PASSWORD},
    )
    return {"authorization": f"Bearer {login.json()['access_token']}"}


# ── Plugin listing ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_list_plugins_includes_injected(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    response = await client.get("/api/v1/plugins", headers=headers)
    assert response.status_code == 200
    ids = {p["id"] for p in response.json()}
    assert "testplug" in ids


# ── Settings schema ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_settings_schema_returns_pydantic_schema(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    response = await client.get(
        "/api/v1/plugins/testplug/settings/schema", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["plugin_id"] == "testplug"
    schema = body["schema"]
    assert schema is not None
    assert "enabled" in schema["properties"]
    assert "threshold" in schema["properties"]
    # Defaults are hydrated for the UI's empty-state.
    assert body["defaults"] == {"enabled": True, "threshold": 10}


@pytest.mark.asyncio
async def test_settings_schema_for_plugin_without_schema(
    client: AsyncClient,
) -> None:
    """A plugin that didn't declare a settings_schema returns None."""
    headers = await _admin_headers(client)
    # Inject a second plugin with no settings_schema attr override.
    loader = get_plugin_loader()
    from app.plugins.contracts import Plugin

    class Plain(Plugin):
        settings_schema = None

    manifest = PluginManifest(
        id="plain",
        name="Plain",
        version="0.1.0",
        type=PluginType.GENERIC,
        backend_entry="__init__.py",
    )
    context = PluginContext(
        manifest=manifest,
        directory=Path("/tmp"),
        registry=get_registry(),
        event_bus=get_event_bus(),
    )
    loader._plugins["plain"] = LoadedPlugin(  # noqa: SLF001
        manifest=manifest, context=context, instance=Plain(context)
    )

    response = await client.get(
        "/api/v1/plugins/plain/settings/schema", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["schema"] is None


# ── Settings persistence ───────────────────────────────────────
@pytest.mark.asyncio
async def test_put_settings_validates_against_schema(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    response = await client.put(
        "/api/v1/plugins/testplug/settings",
        headers=headers,
        json={"values": {"enabled": False, "threshold": 50}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["values"] == {"enabled": False, "threshold": 50}


@pytest.mark.asyncio
async def test_put_settings_rejects_invalid_threshold(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    response = await client.put(
        "/api/v1/plugins/testplug/settings",
        headers=headers,
        json={"values": {"threshold": 200}},  # out of range
    )
    assert response.status_code == 422
    assert "invalid" in response.text.lower()


@pytest.mark.asyncio
async def test_get_settings_returns_null_when_not_configured(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    response = await client.get(
        "/api/v1/plugins/testplug/settings", headers=headers
    )
    assert response.status_code == 200
    assert response.json() is None


@pytest.mark.asyncio
async def test_get_settings_returns_persisted_values(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    await client.put(
        "/api/v1/plugins/testplug/settings",
        headers=headers,
        json={"values": {"threshold": 5}, "notes": "trial run"},
    )
    response = await client.get(
        "/api/v1/plugins/testplug/settings", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    # ``threshold`` overridden, ``enabled`` defaulted by the schema.
    assert body["values"] == {"enabled": True, "threshold": 5}
    assert body["notes"] == "trial run"


@pytest.mark.asyncio
async def test_settings_for_unknown_plugin_is_404(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    response = await client.put(
        "/api/v1/plugins/ghost/settings",
        headers=headers,
        json={"values": {}},
    )
    assert response.status_code == 404


# ── Gallery ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_gallery_returns_feed_with_installed_annotation(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    response = await client.get("/api/v1/plugins/gallery", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    ids = {p["id"] for p in body["plugins"]}
    # Two well-formed entries; malformed one was silently skipped.
    assert ids == {"fingerprint", "testplug"}
    installed_map = {p["id"]: p["installed"] for p in body["plugins"]}
    assert installed_map["testplug"] is True  # we injected it
    assert installed_map["fingerprint"] is False


@pytest.mark.asyncio
async def test_gallery_unreachable_returns_ok_false(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    client._gallery_state["exc"] = httpx.ConnectError("dns gone")  # type: ignore[attr-defined]
    response = await client.get("/api/v1/plugins/gallery", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["plugins"] == []
    assert "unreachable" in (body["detail"] or "").lower()


@pytest.mark.asyncio
async def test_gallery_disabled_when_url_empty(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operator sets AUDITARR_PLUGIN_GALLERY_URL="" to opt out."""
    from app.core.settings import get_settings

    monkeypatch.setenv("AUDITARR_PLUGIN_GALLERY_URL", "")
    get_settings.cache_clear()

    headers = await _admin_headers(client)
    response = await client.get("/api/v1/plugins/gallery", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "disabled" in (body["detail"] or "").lower()


# ── Scaffolder ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_scaffolder_produces_passing_skeleton(tmp_path: Path) -> None:
    """End-to-end: run ``plugin-new``, then assert the scaffolded test passes."""
    import subprocess
    import sys

    target = tmp_path / "plugins"
    cmd = [
        sys.executable,
        "-m",
        "app.cli",
        "plugin-new",
        "my-plugin",
        "--target-dir",
        str(target),
    ]
    backend_root = Path(__file__).resolve().parents[2]
    env = {
        **__import__("os").environ,
        "PYTHONPATH": str(backend_root),
        "AUDITARR_SECRET_KEY": "test-key-must-be-at-least-sixteen-chars",
        "AUDITARR_DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "AUDITARR_REDIS_URL": "redis://localhost:6379/15",
    }
    result = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    plugin_dir = target / "my-plugin"
    assert (plugin_dir / "manifest.json").is_file()
    assert (plugin_dir / "__init__.py").is_file()
    assert (plugin_dir / "tests" / "test_plugin.py").is_file()

    manifest = json.loads((plugin_dir / "manifest.json").read_text())
    assert manifest["id"] == "my-plugin"
    assert manifest["type"] == "generic"

    # The scaffolded test should pass when pointed at the backend.
    pytest_cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--no-header",
        str(plugin_dir / "tests"),
    ]
    result = subprocess.run(pytest_cmd, env=env, capture_output=True, text=True)
    assert result.returncode == 0, (
        f"Scaffolded test failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )


@pytest.mark.asyncio
async def test_scaffolder_rejects_invalid_slug(tmp_path: Path) -> None:
    import subprocess
    import sys

    backend_root = Path(__file__).resolve().parents[2]
    env = {
        **__import__("os").environ,
        "PYTHONPATH": str(backend_root),
        "AUDITARR_SECRET_KEY": "test-key-must-be-at-least-sixteen-chars",
        "AUDITARR_DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "AUDITARR_REDIS_URL": "redis://localhost:6379/15",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.cli",
            "plugin-new",
            "Bad Slug!",
            "--target-dir",
            str(tmp_path / "plugins"),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "invalid slug" in result.stderr.lower()
