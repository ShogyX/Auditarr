"""Integration API integration tests.

We register a stub :class:`IntegrationProvider` directly into the service
registry so the API surface can be exercised without depending on the Plex
plugin being discovered (which requires the plugin loader to scan disk).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.events.bus import get_event_bus
from app.integrations.types import (
    DiscoveredLibrary,
    HealthReport,
    IntegrationConfig,
    IntegrationProvider,
    TagSync,
)
from app.main import create_app
from app.models.user import User
from app.security.secrets import reset_secret_box
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


class StubProvider:
    kind = "stub"
    label = "Stub"
    config_schema = {
        "type": "object",
        "required": ["base_url"],
        "properties": {"base_url": {"type": "string"}},
    }
    secret_fields = ("token",)

    def __init__(self) -> None:
        self._next_health: HealthReport = HealthReport(status="ok", detail="all good")

    async def healthcheck(self, _config: IntegrationConfig) -> HealthReport:
        return self._next_health

    async def discover_libraries(self, _config: IntegrationConfig) -> list[DiscoveredLibrary]:
        return [
            DiscoveredLibrary(
                upstream_id="42",
                name="Stub Movies",
                kind="movies",
                root_path="/media/stub-movies",
            )
        ]

    async def sync_tags(self, _config: IntegrationConfig) -> list[TagSync]:
        return []

    async def fetch_playback_events(
        self, _config: IntegrationConfig, _since
    ) -> list:
        # Stage 16: this stub doesn't exercise playback telemetry.
        return []

    # Stage 07 / Stage 08 protocol additions — required so the
    # ``runtime_checkable`` isinstance check below passes.
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

    # v1.9 Stage 5.1 — trigger_search stub for runtime_checkable.
    async def trigger_search(self, _config, _media_file_path):  # noqa: ANN001, ANN202
        from app.integrations.types import SearchTriggerResult

        return SearchTriggerResult(status="error", detail="stub")


# Make sure isinstance(StubProvider(), IntegrationProvider) succeeds.
assert isinstance(StubProvider(), IntegrationProvider)


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "integrations.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    from app.core.registry import get_registry
    from app.core.settings import get_settings

    get_settings.cache_clear()
    reset_secret_box()

    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001
    bus = get_event_bus()
    bus.clear()

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # Register stub provider directly into the registry that the API uses.
    registry = get_registry()
    registry.register_capability("integration.stub", StubProvider())

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
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
        reset_secret_box()


async def _admin_headers(client: AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "a@example.com",
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


@pytest.mark.asyncio
async def test_list_kinds_includes_stub(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    response = await client.get("/api/v1/integrations/kinds", headers=headers)
    assert response.status_code == 200
    kinds = {k["kind"] for k in response.json()}
    assert "stub" in kinds


@pytest.mark.asyncio
async def test_create_get_list_delete(client: AsyncClient) -> None:
    headers = await _admin_headers(client)

    create = await client.post(
        "/api/v1/integrations",
        headers=headers,
        json={
            "name": "Stub Prod",
            "kind": "stub",
            "config": {"base_url": "http://stub.local"},
            "secrets": {"token": "sekrit"},
        },
    )
    assert create.status_code == 201, create.text
    integration_id = create.json()["id"]
    # Secrets must NOT be returned in any response.
    assert "secrets" not in create.json()
    assert "secrets_ciphertext" not in create.json()
    assert create.json()["has_secrets"] is True

    listing = await client.get("/api/v1/integrations", headers=headers)
    assert {row["id"] for row in listing.json()} == {integration_id}

    fetched = await client.get(
        f"/api/v1/integrations/{integration_id}", headers=headers
    )
    assert fetched.status_code == 200
    assert fetched.json()["has_secrets"] is True

    delete = await client.delete(
        f"/api/v1/integrations/{integration_id}", headers=headers
    )
    assert delete.status_code == 204
    assert (
        await client.get("/api/v1/integrations", headers=headers)
    ).json() == []


@pytest.mark.asyncio
async def test_create_rejects_missing_required_config(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/integrations",
        headers=headers,
        json={
            "name": "Bad",
            "kind": "stub",
            "config": {},  # missing base_url
            "secrets": {"token": "x"},
        },
    )
    assert response.status_code == 422
    assert "base_url" in str(response.json())


@pytest.mark.asyncio
async def test_create_rejects_missing_secret(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/integrations",
        headers=headers,
        json={
            "name": "Bad",
            "kind": "stub",
            "config": {"base_url": "x"},
            "secrets": {},
        },
    )
    assert response.status_code == 422
    assert "token" in str(response.json()).lower()


@pytest.mark.asyncio
async def test_create_rejects_unknown_kind(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/integrations",
        headers=headers,
        json={
            "name": "Bad",
            "kind": "no-such-thing",
            "config": {},
            "secrets": {},
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_healthcheck_writes_state(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    create = await client.post(
        "/api/v1/integrations",
        headers=headers,
        json={
            "name": "Stub Prod",
            "kind": "stub",
            "config": {"base_url": "http://stub.local"},
            "secrets": {"token": "sekrit"},
        },
    )
    integration_id = create.json()["id"]

    health = await client.post(
        f"/api/v1/integrations/{integration_id}/healthcheck", headers=headers
    )
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"

    # Latest health is persisted.
    refresh = await client.get(
        f"/api/v1/integrations/{integration_id}", headers=headers
    )
    assert refresh.json()["health_status"] == "ok"
    assert refresh.json()["health_checked_at"] is not None


@pytest.mark.asyncio
async def test_discover_libraries(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    create = await client.post(
        "/api/v1/integrations",
        headers=headers,
        json={
            "name": "Stub Prod",
            "kind": "stub",
            "config": {"base_url": "http://stub.local"},
            "secrets": {"token": "sekrit"},
        },
    )
    integration_id = create.json()["id"]

    response = await client.get(
        f"/api/v1/integrations/{integration_id}/libraries", headers=headers
    )
    assert response.status_code == 200
    libs = response.json()
    assert len(libs) == 1
    assert libs[0]["root_path"] == "/media/stub-movies"


@pytest.mark.asyncio
async def test_non_admin_cannot_mutate(client: AsyncClient) -> None:
    # Register a non-admin user.
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "u@example.com",
            "username": "user1",
            "password": PASSWORD,
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "user1", "password": PASSWORD},
    )
    headers = {"authorization": f"Bearer {login.json()['access_token']}"}

    response = await client.post(
        "/api/v1/integrations",
        headers=headers,
        json={
            "name": "x",
            "kind": "stub",
            "config": {"base_url": "y"},
            "secrets": {"token": "z"},
        },
    )
    assert response.status_code == 403


def _stub_provider():
    from app.core.registry import get_registry

    return get_registry().providers_for("integration.stub")[0]


@pytest.mark.asyncio
async def test_create_blocked_when_upstream_unreachable(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    _stub_provider()._next_health = HealthReport(
        status="error", detail="connection refused"
    )

    response = await client.post(
        "/api/v1/integrations",
        headers=headers,
        json={
            "name": "Will fail",
            "kind": "stub",
            "config": {"base_url": "http://nope"},
            "secrets": {"token": "x"},
        },
    )
    assert response.status_code == 422
    assert "connection refused" in str(response.json())

    # Nothing got saved.
    listing = await client.get("/api/v1/integrations", headers=headers)
    assert listing.json() == []


@pytest.mark.asyncio
async def test_create_skip_preflight_saves_anyway(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    _stub_provider()._next_health = HealthReport(status="error", detail="down")

    response = await client.post(
        "/api/v1/integrations?skip_preflight=true",
        headers=headers,
        json={
            "name": "Skipped",
            "kind": "stub",
            "config": {"base_url": "http://x"},
            "secrets": {"token": "x"},
        },
    )
    assert response.status_code == 201
    # Health stays unknown since we skipped the post-save healthcheck too.
    assert response.json()["health_status"] == "unknown"


@pytest.mark.asyncio
async def test_test_endpoint_returns_provider_report(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    _stub_provider()._next_health = HealthReport(status="ok", detail="online")

    response = await client.post(
        "/api/v1/integrations/test",
        headers=headers,
        json={
            "name": "candidate",
            "kind": "stub",
            "config": {"base_url": "http://x"},
            "secrets": {"token": "y"},
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["detail"] == "online"
    # Nothing was persisted.
    listing = await client.get("/api/v1/integrations", headers=headers)
    assert listing.json() == []


@pytest.mark.asyncio
async def test_discover_libraries_blocked_when_unhealthy(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    # First create with a healthy provider.
    _stub_provider()._next_health = HealthReport(status="ok", detail="ok")
    create = await client.post(
        "/api/v1/integrations",
        headers=headers,
        json={
            "name": "stub-1",
            "kind": "stub",
            "config": {"base_url": "http://x"},
            "secrets": {"token": "y"},
        },
    )
    integration_id = create.json()["id"]

    # Now flip the provider to error and attempt to discover.
    _stub_provider()._next_health = HealthReport(
        status="error", detail="went away"
    )
    response = await client.get(
        f"/api/v1/integrations/{integration_id}/libraries", headers=headers
    )
    assert response.status_code == 422
    assert "not reachable" in str(response.json()).lower()
