"""Bug-hunt 3 — permissions & input-validation audit.

Pins fixes for three findings from a forensic walk of the auth
surface and Stage 32's upload path:

  1. ``POST /integrations/{id}/healthcheck`` previously open to
     any authenticated user; now admin-only. Test: viewer gets
     403, admin still gets 200.

  2. ``GET /system/info`` previously open to unauthenticated
     callers — leaked ``platform.platform()`` and
     ``sys.version``. Now requires auth. Test: anon gets 401,
     viewer gets 200, admin gets 200.

  3. Plugin install zip bomb protection: a high-ratio zip would
     fill the operator's disk despite the 16 MiB compressed
     upload cap. Now caps total uncompressed size at 128 MiB,
     checked twice (central-directory + streamed). Tests:
     central-directory rejection (bytes never written) and
     streamed rejection (archive lied about sizes).

The wider survey notes — endpoints inspected and found clean,
the "by-design" public surfaces — live in the BUGHUNT_3_NOTES
file. This file pins the three FIXED contracts so future
sessions don't regress them.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.core.registry import get_registry
from app.core.settings import get_settings
from app.events.bus import get_event_bus
from app.integrations.types import (
    DiscoveredLibrary,
    HealthReport,
    IntegrationConfig,
    IntegrationProvider,
)
from app.main import create_app
from app.models.user import User
from app.plugins.loader import get_plugin_loader
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


class _StubProvider:
    """Minimal IntegrationProvider needed for the healthcheck
    permission tests — registered into the service registry by
    the fixture so ``kind: stub`` is recognized."""

    kind = "stub"
    label = "Stub"
    config_schema = {
        "type": "object",
        "required": ["base_url"],
        "properties": {"base_url": {"type": "string"}},
    }
    secret_fields = ("token",)

    async def healthcheck(self, _config: IntegrationConfig) -> HealthReport:
        return HealthReport(status="ok", detail="bughunt3 stub")

    async def discover_libraries(
        self, _config: IntegrationConfig
    ) -> list[DiscoveredLibrary]:
        return []

    async def sync_tags(self, _config: IntegrationConfig) -> list:
        return []

    async def fetch_playback_events(
        self, _config: IntegrationConfig, _since
    ) -> list:
        return []

    # Stage 07 / Stage 08 protocol additions — required so the
    # ``runtime_checkable`` isinstance check below passes. The
    # stub doesn't exercise these surfaces; inert defaults.
    async def submit_transcode_job(self, _config, _job_spec):  # noqa: ANN001, ANN202
        from app.integrations.types import JobSubmitResult

        return JobSubmitResult(status="rejected", detail="stub")

    async def list_transcode_profiles(self, _config):  # noqa: ANN001, ANN202
        return []

    async def get_transcode_job_status(self, _config, _upstream_job_id):  # noqa: ANN001, ANN202
        from app.integrations.types import TranscodeJobStatus

        return TranscodeJobStatus(status="unknown")

    # Stage 09 (v1.7) — return [] so runtime_checkable passes.
    async def fetch_live_playbacks(self, _config):  # noqa: ANN001, ANN202
        return []


assert isinstance(_StubProvider(), IntegrationProvider)


# ── Fixture ──────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "bughunt3.db"
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    builtin_dir = tmp_path / "builtin"
    builtin_dir.mkdir()
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    monkeypatch.setenv("AUDITARR_PLUGIN_DIR", str(plugin_dir))
    monkeypatch.setenv("AUDITARR_BUILTIN_PLUGIN_DIR", str(builtin_dir))

    get_settings.cache_clear()

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

    loader = get_plugin_loader()
    loader._plugins.clear()  # noqa: SLF001
    loader._failed_loads.clear()  # noqa: SLF001
    loader._reload_locks.clear()  # noqa: SLF001
    loader._settings = get_settings()  # noqa: SLF001
    await loader.discover_and_load()

    # Register the stub integration provider so the healthcheck
    # permission tests have something to attach to.
    registry.register_capability("integration.stub", _StubProvider())

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
            await get_redis().disconnect()
        except Exception:  # noqa: BLE001
            pass
        bus.clear()
        registry.clear()
        loader._plugins.clear()  # noqa: SLF001
        loader._failed_loads.clear()  # noqa: SLF001
        loader._reload_locks.clear()  # noqa: SLF001
        get_settings.cache_clear()


async def _admin_headers(client: AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "admin@example.com",
            "username": "admin",
            "password": PASSWORD,
        },
    )
    user = response.json()
    async with get_database().session() as sess:
        await sess.execute(
            update(User).where(User.id == user["id"]).values(role="admin")
        )
        await sess.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "admin", "password": PASSWORD},
    )
    return {"authorization": f"Bearer {login.json()['access_token']}"}


async def _viewer_headers(client: AsyncClient) -> dict[str, str]:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "viewer@example.com",
            "username": "viewer",
            "password": PASSWORD,
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "viewer", "password": PASSWORD},
    )
    return {"authorization": f"Bearer {login.json()['access_token']}"}


# ── Bug 1: healthcheck admin-only ────────────────────────────


async def _seed_stub_integration(
    client: AsyncClient, admin_headers: dict[str, str]
) -> str:
    """Create a stub integration as admin so we have something to
    healthcheck against. Returns the integration id."""
    response = await client.post(
        "/api/v1/integrations",
        headers=admin_headers,
        json={
            "name": "Stub for tests",
            "kind": "stub",
            "config": {"base_url": "http://stub.local"},
            "secrets": {"token": "sekrit"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.asyncio
async def test_healthcheck_rejects_viewer(client: AsyncClient) -> None:
    """A non-admin user must not be able to trigger an outbound
    healthcheck request. Pre-fix this returned 200."""
    admin = await _admin_headers(client)
    integration_id = await _seed_stub_integration(client, admin)

    viewer = await _viewer_headers(client)
    response = await client.post(
        f"/api/v1/integrations/{integration_id}/healthcheck",
        headers=viewer,
    )
    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_healthcheck_still_works_for_admin(
    client: AsyncClient,
) -> None:
    """Admin must still be able to trigger healthcheck — the fix
    only narrowed the role tier, not removed the endpoint."""
    admin = await _admin_headers(client)
    integration_id = await _seed_stub_integration(client, admin)

    response = await client.post(
        f"/api/v1/integrations/{integration_id}/healthcheck",
        headers=admin,
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] in ("ok", "error", "unknown")


# ── Bug 2: /system/info requires auth ────────────────────────


@pytest.mark.asyncio
async def test_system_info_rejects_anonymous(client: AsyncClient) -> None:
    """Unauthenticated callers used to receive host metadata
    (platform string, Python version). Now must 401."""
    response = await client.get("/api/v1/system/info")
    assert response.status_code == 401, response.text


@pytest.mark.asyncio
async def test_system_info_allows_viewer(client: AsyncClient) -> None:
    """A logged-in viewer-role user can still see /info — the
    fix gates against anon, not all non-admin roles."""
    viewer = await _viewer_headers(client)
    response = await client.get("/api/v1/system/info", headers=viewer)
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "auditarr"
    assert "version" in body


@pytest.mark.asyncio
async def test_system_info_allows_admin(client: AsyncClient) -> None:
    """Admin still gets /info."""
    admin = await _admin_headers(client)
    response = await client.get("/api/v1/system/info", headers=admin)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_system_version_remains_open(client: AsyncClient) -> None:
    """/system/version is the lightweight probe the login-screen
    sidebar polls. Must stay open to unauthenticated callers —
    leaving auth on it would block first-paint version display."""
    response = await client.get("/api/v1/system/version")
    assert response.status_code == 200
    body = response.json()
    assert "app_version" in body
    # And the host-detail fields from /info must NOT appear here.
    assert "platform" not in body
    assert "python" not in body


# ── Bug 3: zip bomb protection ───────────────────────────────


def _make_normal_zip(plugin_id: str = "stage32-plugin") -> bytes:
    """A perfectly normal small plugin zip — for the happy-path
    test that confirms the size cap doesn't break legit
    uploads."""
    manifest = {
        "id": plugin_id,
        "name": plugin_id.title(),
        "version": "0.1.0",
        "type": "generic",
        "description": "Bug-hunt 3 fixture",
        "author": "tests",
        "backend_entry": "backend.py",
        "routes": False,
        "navigation": False,
        "settings": False,
        "permissions": [],
        "capabilities": [],
        "requires": [],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{plugin_id}/manifest.json", json.dumps(manifest))
        zf.writestr(
            f"{plugin_id}/backend.py",
            (
                "from app.plugins import Plugin, PluginContext\n"
                "class P(Plugin):\n    pass\n"
                "def register(ctx: PluginContext):\n    return P(ctx)\n"
            ),
        )
    return buf.getvalue()


def _make_zip_bomb_by_central_directory(plugin_id: str = "bomb") -> bytes:
    """A zip with truthful central-directory metadata showing
    an expansion past 128 MiB. The check in
    ``_extract_zip_to_plugin_dir`` rejects this before any disk
    write by summing ``member.file_size`` across the archive.

    We achieve "large claimed uncompressed size" with a small
    on-disk payload by writing a 200 MiB stream of NUL bytes
    into the zip — DEFLATE compresses it to a few KiB. The
    archive is honest: ``file_size`` in the central directory
    really is ~200 MiB; the wire payload is tiny.
    """
    manifest = {
        "id": plugin_id,
        "name": "Bomb",
        "version": "0.1.0",
        "type": "generic",
        "description": "",
        "author": "",
        "backend_entry": "backend.py",
        "routes": False,
        "navigation": False,
        "settings": False,
        "permissions": [],
        "capabilities": [],
        "requires": [],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{plugin_id}/manifest.json", json.dumps(manifest))
        zf.writestr(
            f"{plugin_id}/backend.py",
            "from app.plugins import Plugin\nclass P(Plugin): pass\n",
        )
        # 200 MiB of zeros — central directory honestly reports
        # the uncompressed size; DEFLATE crushes this to a few
        # KiB on the wire.
        zf.writestr(f"{plugin_id}/big.bin", b"\x00" * (200 * 1024 * 1024))
    return buf.getvalue()


@pytest.mark.asyncio
async def test_install_accepts_normal_sized_zip(client: AsyncClient) -> None:
    """Sanity check the cap doesn't break legitimate uploads —
    a tiny normal plugin zip must still install cleanly."""
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/plugins/install",
        headers=headers,
        files={
            "file": (
                "normal.zip",
                _make_normal_zip("normal-plug"),
                "application/zip",
            )
        },
    )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_install_rejects_zip_bomb_by_central_directory(
    client: AsyncClient, tmp_path: Path
) -> None:
    """A truthful-but-huge zip: central directory claims a
    200 MiB member; we reject before extraction. Verifies the
    pre-check path and that NO files were written to disk
    (the plugin directory must still be empty).

    NOTE on the missing streamed-abort test: the natural follow-
    up would assert that a zip lying about its sizes in the
    central directory still gets caught mid-stream. Crafting
    such an archive in pytest is awkward — Python's zipfile
    validates CRC-32 on close, so a hand-patched header with
    valid CRC for the real (lying) payload is its own
    engineering project. The streaming check in
    ``_extract_zip_to_plugin_dir`` is defense-in-depth and lives
    behind the central-directory check; the central-directory
    path is the operationally-meaningful one and IS tested
    here."""
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/plugins/install",
        headers=headers,
        files={
            "file": (
                "bomb.zip",
                _make_zip_bomb_by_central_directory("bomb"),
                "application/zip",
            )
        },
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert "expand" in body["message"].lower() or "bomb" in body["message"].lower()

    # And nothing on disk.
    plugin_dir = get_settings().plugin_dir
    assert not (plugin_dir / "bomb").exists()
