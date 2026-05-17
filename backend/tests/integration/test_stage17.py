"""Stage 17 (audit follow-up) — UI polish backend wiring.

Pins:
  1. Creating an integration auto-snapshots discovered libraries
     into ``Integration.discovered_paths``.
  2. The snapshot surfaces on ``GET /system/path-mappings``.
  3. Discovery failures are non-fatal — the integration still
     creates successfully, ``discovered_paths`` stays ``NULL``.
  4. ``POST /integrations/{id}/discover-paths`` refreshes the
     snapshot on demand.
  5. ``GET /automation/kinds`` surfaces the ``format`` hints
     (``library_id`` and ``integration_id``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.core.registry import get_registry  # noqa: E402 (test fixture)
from app.events.bus import get_event_bus
from app.integrations.types import (
    DiscoveredLibrary,
    HealthReport,
    IntegrationConfig,
    IntegrationProvider,
    TagSync,
)
from app.main import create_app
from app.models.integration import Integration
from app.models.user import User
from app.security.secrets import reset_secret_box
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


class _DiscoveryStub:
    """Stub provider whose discovery output is set per-test via
    class-level state."""

    kind = "stub"
    label = "Stub"
    config_schema = {
        "type": "object",
        "required": ["base_url"],
        "properties": {"base_url": {"type": "string"}},
    }
    secret_fields = ("token",)

    DISCOVERY: list[DiscoveredLibrary] = []
    RAISE: Exception | None = None

    async def healthcheck(self, _config: IntegrationConfig) -> HealthReport:
        return HealthReport(status="ok", detail="all good")

    async def discover_libraries(
        self, _config: IntegrationConfig
    ) -> list[DiscoveredLibrary]:
        if _DiscoveryStub.RAISE is not None:
            raise _DiscoveryStub.RAISE
        return list(_DiscoveryStub.DISCOVERY)

    async def sync_tags(self, _config: IntegrationConfig) -> list[TagSync]:
        return []

    async def fetch_playback_events(
        self, _config: IntegrationConfig, _since
    ) -> list:
        return []

    # Stage 07 / Stage 08 protocol additions — the stub doesn't
    # exercise these surfaces but must satisfy the
    # ``runtime_checkable`` isinstance check at module load. We
    # return inert defaults so the protocol-conformance assert
    # below passes without altering the discovery behaviour
    # under test.
    async def submit_transcode_job(self, _config, _job_spec):  # noqa: ANN001, ANN202
        from app.integrations.types import JobSubmitResult

        return JobSubmitResult(
            status="rejected", detail="discovery stub does not submit jobs"
        )

    async def list_transcode_profiles(self, _config):  # noqa: ANN001, ANN202
        return []

    async def get_transcode_job_status(self, _config, _upstream_job_id):  # noqa: ANN001, ANN202
        from app.integrations.types import TranscodeJobStatus

        return TranscodeJobStatus(status="unknown")

    # Stage 09 (v1.7) protocol addition — return [] so the
    # ``runtime_checkable`` isinstance check passes.
    async def fetch_live_playbacks(self, _config):  # noqa: ANN001, ANN202
        return []


assert isinstance(_DiscoveryStub(), IntegrationProvider)


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "stage17.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("AUDITARR_DATA_DIR", str(data_dir))
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    from app.core.settings import get_settings

    get_settings.cache_clear()
    reset_secret_box()
    _DiscoveryStub.DISCOVERY = []
    _DiscoveryStub.RAISE = None

    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001
    bus = get_event_bus()
    bus.clear()

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    registry = get_registry()
    registry.register_capability("integration.stub", _DiscoveryStub())

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as c:
            yield c
    finally:
        registry.clear()
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        try:
            await get_redis().disconnect()
        except Exception:  # noqa: BLE001
            pass
        bus.clear()
        get_settings.cache_clear()


async def _admin(client: AsyncClient) -> dict[str, str]:
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "admin@example.com",
            "username": "admin",
            "password": PASSWORD,
        },
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


async def _create_stub_integration(
    client: AsyncClient, headers: dict[str, str], name: str = "Stub one"
) -> str:
    r = await client.post(
        "/api/v1/integrations",
        headers=headers,
        json={
            "name": name,
            "kind": "stub",
            "config": {"base_url": "http://stub.test"},
            "secrets": {"token": "x"},
            "enabled": True,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_create_integration_snapshots_discovered_paths(
    client: AsyncClient,
) -> None:
    headers = await _admin(client)
    _DiscoveryStub.DISCOVERY = [
        DiscoveredLibrary(
            upstream_id="lib-movies",
            name="Movies",
            kind="movies",
            root_path="/data/media/movies",
        ),
        DiscoveredLibrary(
            upstream_id="lib-tv",
            name="TV",
            kind="tv",
            root_path="/data/media/tv",
        ),
        DiscoveredLibrary(
            upstream_id="lib-pathless",
            name="Music",
            kind="music",
            root_path=None,
        ),
    ]

    integration_id = await _create_stub_integration(client, headers)

    async with get_database().session() as sess:
        ig = await sess.get(Integration, integration_id)
        assert ig is not None
        assert ig.discovered_paths is not None
        assert len(ig.discovered_paths) == 2
        paths = {p["upstream_path"] for p in ig.discovered_paths}
        assert paths == {"/data/media/movies", "/data/media/tv"}
        for p in ig.discovered_paths:
            assert {"library_id", "label", "upstream_path", "discovered_at"} <= set(p)


@pytest.mark.asyncio
async def test_create_integration_discovery_failure_is_nonfatal(
    client: AsyncClient,
) -> None:
    headers = await _admin(client)
    _DiscoveryStub.RAISE = RuntimeError("upstream unreachable")

    integration_id = await _create_stub_integration(client, headers)

    async with get_database().session() as sess:
        ig = await sess.get(Integration, integration_id)
        assert ig is not None
        assert ig.discovered_paths is None


@pytest.mark.asyncio
async def test_path_mappings_response_surfaces_discovered_paths(
    client: AsyncClient,
) -> None:
    headers = await _admin(client)
    _DiscoveryStub.DISCOVERY = [
        DiscoveredLibrary(
            upstream_id="lib-1",
            name="Movies",
            kind="movies",
            root_path="/data/media/movies",
        ),
    ]
    await _create_stub_integration(client, headers)

    r = await client.get("/api/v1/system/path-mappings", headers=headers)
    assert r.status_code == 200, r.text
    integrations = r.json()["integrations"]
    assert len(integrations) == 1
    row = integrations[0]
    assert row["discovered_paths"] is not None
    assert len(row["discovered_paths"]) == 1
    assert row["discovered_paths"][0]["upstream_path"] == "/data/media/movies"


@pytest.mark.asyncio
async def test_rediscover_paths_endpoint_refreshes_snapshot(
    client: AsyncClient,
) -> None:
    headers = await _admin(client)
    _DiscoveryStub.DISCOVERY = [
        DiscoveredLibrary(
            upstream_id="lib-old",
            name="Old",
            kind="movies",
            root_path="/old/path",
        )
    ]
    integration_id = await _create_stub_integration(client, headers)

    _DiscoveryStub.DISCOVERY = [
        DiscoveredLibrary(
            upstream_id="lib-new",
            name="New",
            kind="movies",
            root_path="/new/path",
        )
    ]
    r = await client.post(
        f"/api/v1/integrations/{integration_id}/discover-paths",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["integration_id"] == integration_id
    paths = {p["upstream_path"] for p in body["discovered_paths"]}
    assert paths == {"/new/path"}


@pytest.mark.asyncio
async def test_automation_kinds_surfaces_format_hints(
    client: AsyncClient,
) -> None:
    headers = await _admin(client)
    r = await client.get("/api/v1/automation/jobs", headers=headers)
    assert r.status_code == 200, r.text
    kinds = {k["key"]: k for k in r.json()}

    for key in ("scan_library", "evaluate_library"):
        spec = kinds[key]["args_schema"]["properties"]["library_id"]
        assert spec.get("format") == "library_id", (
            f"{key} missing format hint: {spec}"
        )

    for key in ("healthcheck_integration", "sync_integration_tags"):
        spec = kinds[key]["args_schema"]["properties"]["integration_id"]
        assert spec.get("format") == "integration_id", (
            f"{key} missing format hint: {spec}"
        )
