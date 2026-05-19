"""Stage 19 (audit follow-up) — webhook ingress + per-integration
HMAC + dispatcher routing tests.

Pins:
  1. ``POST /integrations/{id}/webhook-secret`` returns the plaintext
     ONCE and stores ciphertext.
  2. Webhook receive 401s when the integration has no secret.
  3. Webhook receive 401s on missing signature.
  4. Webhook receive 401s on mismatched signature.
  5. Sonarr Download routes to ``reprobe`` and finds the file by
     remapped path.
  6. Sonarr EpisodeFileDelete routes to ``remove`` and marks the
     row orphaned.
  7. Unknown kind 404s.
  8. Wrong-kind for integration (e.g. /webhooks/sonarr/<radarr-id>)
     400s.
  9. Test events / unknown event types return 200 with action=ignored.
 10. Reprobe handler no-ops gracefully when the path isn't in the DB
     (we don't fall through to a scan).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
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
from app.models.library import Library
from app.models.media import MediaFile
from app.models.user import User
from app.security.secrets import reset_secret_box
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


class _StubProvider:
    kind = "stub"
    label = "Stub"
    config_schema = {
        "type": "object",
        "required": ["base_url"],
        "properties": {"base_url": {"type": "string"}},
    }
    secret_fields = ("token",)

    async def healthcheck(self, _config: IntegrationConfig) -> HealthReport:
        return HealthReport(status="ok", detail="ok")

    async def discover_libraries(
        self, _config: IntegrationConfig
    ) -> list[DiscoveredLibrary]:
        return []

    async def sync_tags(self, _config: IntegrationConfig) -> list[TagSync]:
        return []

    async def fetch_playback_events(self, _config, _since) -> list:
        return []

    # Stage 07 / Stage 08 protocol additions — inherited by the
    # Sonarr/Radarr subclasses; needed for ``runtime_checkable``
    # isinstance to pass.
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


class _SonarrStub(_StubProvider):
    kind = "sonarr"
    label = "Sonarr stub"


class _RadarrStub(_StubProvider):
    kind = "radarr"
    label = "Radarr stub"


assert isinstance(_SonarrStub(), IntegrationProvider)
assert isinstance(_RadarrStub(), IntegrationProvider)


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "stage19.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("AUDITARR_DATA_DIR", str(data_dir))
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

    registry = get_registry()
    registry.register_capability("integration.sonarr", _SonarrStub())
    registry.register_capability("integration.radarr", _RadarrStub())

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


async def _create_integration(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    kind: str = "sonarr",
    name: str | None = None,
    path_mappings: list[dict] | None = None,
) -> str:
    body = {
        "name": name or f"{kind} stub",
        "kind": kind,
        "config": {"base_url": "http://stub.test"},
        "secrets": {"token": "x"},
        "enabled": True,
    }
    if path_mappings is not None:
        body["config"]["path_mappings"] = path_mappings
    r = await client.post(
        "/api/v1/integrations",
        headers=headers,
        json=body,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _set_webhook_secret(
    client: AsyncClient, headers: dict[str, str], integration_id: str
) -> str:
    r = await client.post(
        f"/api/v1/integrations/{integration_id}/webhook-secret",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["webhook_secret"]


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()


async def _seed_media_file(library_path: str, file_path: str) -> str:
    """Insert a Library + MediaFile and return the file id."""
    async with get_database().session() as sess:
        lib = Library(name="L", root_path=library_path, kind="tv")
        sess.add(lib)
        await sess.flush()
        mf = MediaFile(
            library_id=lib.id,
            path=file_path,
            relative_path=file_path.split("/")[-1],
            filename=file_path.split("/")[-1],
            extension=file_path.rsplit(".", 1)[-1],
            size_bytes=1024,
            mtime=datetime.now(UTC),
        )
        sess.add(mf)
        await sess.commit()
        return mf.id


# ── 1: webhook-secret endpoint ───────────────────────────────
@pytest.mark.asyncio
async def test_webhook_secret_endpoint_returns_plaintext_once(
    client: AsyncClient,
) -> None:
    headers = await _admin(client)
    integration_id = await _create_integration(client, headers)
    r = await client.post(
        f"/api/v1/integrations/{integration_id}/webhook-secret",
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "webhook_secret" in body
    assert len(body["webhook_secret"]) == 64  # 32 bytes hex
    assert body["webhook_url_suffix"].endswith(integration_id)
    # Re-issuing rotates → new plaintext.
    r2 = await client.post(
        f"/api/v1/integrations/{integration_id}/webhook-secret",
        headers=headers,
    )
    assert r2.json()["webhook_secret"] != body["webhook_secret"]


# ── 1b: webhook-secret refuses for non-receiver kinds ────────
@pytest.mark.asyncio
async def test_webhook_secret_refused_for_non_receiver_kind(
    client: AsyncClient,
) -> None:
    """The receiver only knows sonarr/radarr/plex/jellyfin. Minting
    a webhook URL for any other kind (bazarr, tdarr, virustotal…)
    hands the operator a URL that will 404 on the first delivery."""
    from app.models.integration import Integration
    from app.storage.database import get_database

    headers = await _admin(client)
    # Insert a bazarr integration directly to skip preflight/schema —
    # we only care about the rotate-secret refusal, not the full
    # bazarr create path.
    db = get_database()
    async with db.session() as sess:
        row = Integration(
            name="bazarr-stub",
            kind="bazarr",
            enabled=True,
            config={"base_url": "http://stub.test"},
        )
        sess.add(row)
        await sess.commit()
        integration_id = row.id

    r = await client.post(
        f"/api/v1/integrations/{integration_id}/webhook-secret",
        headers=headers,
    )
    assert r.status_code == 422, r.text
    body = r.json()
    assert "bazarr" in body["message"]
    assert "sonarr" in body["message"]  # supported list cited


# ── 2: no secret on integration → 401 ────────────────────────
@pytest.mark.asyncio
async def test_webhook_no_secret_returns_401(client: AsyncClient) -> None:
    headers = await _admin(client)
    integration_id = await _create_integration(client, headers)
    # No webhook-secret call → ciphertext is NULL.
    r = await client.post(
        f"/api/v1/webhooks/sonarr/{integration_id}",
        json={"eventType": "Test"},
    )
    assert r.status_code == 401


# ── 3: missing signature → 401 ───────────────────────────────
@pytest.mark.asyncio
async def test_webhook_missing_signature_returns_401(
    client: AsyncClient,
) -> None:
    headers = await _admin(client)
    integration_id = await _create_integration(client, headers)
    await _set_webhook_secret(client, headers, integration_id)
    r = await client.post(
        f"/api/v1/webhooks/sonarr/{integration_id}",
        json={"eventType": "Test"},
    )
    assert r.status_code == 401


# ── 4: bad signature → 401 ───────────────────────────────────
@pytest.mark.asyncio
async def test_webhook_bad_signature_returns_401(
    client: AsyncClient,
) -> None:
    headers = await _admin(client)
    integration_id = await _create_integration(client, headers)
    await _set_webhook_secret(client, headers, integration_id)
    body_bytes = json.dumps({"eventType": "Test"}).encode()
    r = await client.post(
        f"/api/v1/webhooks/sonarr/{integration_id}",
        content=body_bytes,
        headers={"X-Auditarr-Signature": "sha256=deadbeef"},
    )
    assert r.status_code == 401


# ── 5: Sonarr Download → reprobe + path remapping ────────────
@pytest.mark.asyncio
async def test_sonarr_download_reprobes_via_remapped_path(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dispatcher calls Scanner.reprobe_one with the remapped
    local path. We monkeypatch reprobe_one to capture calls."""
    headers = await _admin(client)
    integration_id = await _create_integration(client, headers)
    # PUT path mappings via the dedicated endpoint so the stub's
    # config schema (no path_mappings declared) doesn't reject the
    # create payload.
    pm = await client.put(
        f"/api/v1/system/path-mappings/{integration_id}",
        headers=headers,
        json={"mappings": [{"from": "/upstream/tv", "to": "/local/tv"}]},
    )
    assert pm.status_code == 200, pm.text
    await _set_webhook_secret(client, headers, integration_id)
    secret = (
        await client.post(
            f"/api/v1/integrations/{integration_id}/webhook-secret",
            headers=headers,
        )
    ).json()["webhook_secret"]

    # Seed a media file at the LOCAL path that the upstream → local
    # mapping resolves to.
    seeded_id = await _seed_media_file("/local/tv", "/local/tv/show.mkv")

    # Capture the Scanner.reprobe_one call.
    captured: list[str] = []

    from app.services.media.scanner import Scanner

    async def fake_reprobe(self, mf):  # noqa: ARG001
        captured.append(mf.id)
        return mf

    monkeypatch.setattr(Scanner, "reprobe_one", fake_reprobe)

    body_bytes = json.dumps(
        {
            "eventType": "Download",
            "episodeFile": {"path": "/upstream/tv/show.mkv"},
        }
    ).encode()
    r = await client.post(
        f"/api/v1/webhooks/sonarr/{integration_id}",
        content=body_bytes,
        headers={"X-Auditarr-Signature": _sign(body_bytes, secret)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["action"] == "reprobe"
    assert body["paths"] == ["/local/tv/show.mkv"]
    assert captured == [seeded_id]


# ── 6: Sonarr EpisodeFileDelete → mark orphaned ──────────────
@pytest.mark.asyncio
async def test_sonarr_delete_marks_orphaned(
    client: AsyncClient,
) -> None:
    headers = await _admin(client)
    integration_id = await _create_integration(client, headers)
    secret = await _set_webhook_secret(client, headers, integration_id)
    file_id = await _seed_media_file("/local/tv", "/local/tv/old.mkv")

    body_bytes = json.dumps(
        {
            "eventType": "EpisodeFileDelete",
            "episodeFile": {"path": "/local/tv/old.mkv"},
        }
    ).encode()
    r = await client.post(
        f"/api/v1/webhooks/sonarr/{integration_id}",
        content=body_bytes,
        headers={"X-Auditarr-Signature": _sign(body_bytes, secret)},
    )
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "remove"

    async with get_database().session() as sess:
        mf = await sess.get(MediaFile, file_id)
        assert mf is not None
        assert mf.is_orphaned is True


# ── 7: unknown kind 404 ──────────────────────────────────────
@pytest.mark.asyncio
async def test_unknown_kind_returns_404(client: AsyncClient) -> None:
    headers = await _admin(client)
    integration_id = await _create_integration(client, headers)
    r = await client.post(
        f"/api/v1/webhooks/something-weird/{integration_id}",
        json={},
    )
    assert r.status_code == 404


# ── 8: kind-mismatch 400 ─────────────────────────────────────
@pytest.mark.asyncio
async def test_webhook_kind_mismatch_returns_400(
    client: AsyncClient,
) -> None:
    headers = await _admin(client)
    sonarr_id = await _create_integration(
        client, headers, kind="sonarr", name="sonarr 1"
    )
    # Create a radarr integration too so both routes are
    # configured; the test below sends the wrong kind to the
    # radarr endpoint and expects rejection. The id isn't
    # used directly — its existence is the side-effect we
    # depend on.
    await _create_integration(
        client, headers, kind="radarr", name="radarr 1"
    )
    await _set_webhook_secret(client, headers, sonarr_id)
    # Use the sonarr integration_id but hit the radarr endpoint.
    r = await client.post(
        f"/api/v1/webhooks/radarr/{sonarr_id}",
        json={"eventType": "Download"},
    )
    assert r.status_code == 400


# ── 9: Test / unknown event types → 200 ignored ──────────────
@pytest.mark.asyncio
async def test_test_event_returns_200_ignored(client: AsyncClient) -> None:
    headers = await _admin(client)
    integration_id = await _create_integration(client, headers)
    secret = await _set_webhook_secret(client, headers, integration_id)
    body_bytes = json.dumps({"eventType": "Test"}).encode()
    r = await client.post(
        f"/api/v1/webhooks/sonarr/{integration_id}",
        content=body_bytes,
        headers={"X-Auditarr-Signature": _sign(body_bytes, secret)},
    )
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "ignored"


# ── 10: reprobe of unknown path is a graceful noop ───────────
@pytest.mark.asyncio
async def test_reprobe_unknown_path_no_crash(client: AsyncClient) -> None:
    headers = await _admin(client)
    integration_id = await _create_integration(client, headers)
    secret = await _set_webhook_secret(client, headers, integration_id)
    body_bytes = json.dumps(
        {
            "eventType": "Download",
            "episodeFile": {"path": "/never/seen/before.mkv"},
        }
    ).encode()
    r = await client.post(
        f"/api/v1/webhooks/sonarr/{integration_id}",
        content=body_bytes,
        headers={"X-Auditarr-Signature": _sign(body_bytes, secret)},
    )
    # The dispatcher decides "action=reprobe" (it matched the event
    # type), the handler logs + skips because the path isn't in DB.
    # Endpoint returns 200; we don't surface "ignored" because the
    # event WAS valid.
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "reprobe"
