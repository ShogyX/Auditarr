"""v1.9 Stage 7 — discovery endpoints + tag filtering.

Covers:

  * 7.1 POST /api/v1/integrations/{id}/discover-path-mappings
    — Sonarr/Radarr/Bazarr root folder probing, library
    matching by basename suffix, unmatched suggestions
    surface with confidence="none".
  * 7.1 POST /api/v1/integrations/{id}/discover-webhook-sources
    — surfaces IPs from recent webhook.received audit rows.
  * 7.2 GET /api/v1/integrations/{id}/upstream-tags — lists
    sonarr/radarr tags, Bazarr synthesized tags, empty for
    others.
  * 7.2 Tag allowlist / denylist filtering in
    IntegrationTagSync.apply.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.events.bus import get_event_bus
from app.integrations.types import (
    HealthReport,
    IntegrationProvider,
    SearchTriggerResult,
    TagSync,
)
from app.main import create_app
from app.models.audit_log import AuditLogEntry
from app.models.integration import Integration
from app.models.library import Library
from app.models.media import MediaFile
from app.models.tag import MediaTag
from app.models.user import User
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


# ── Stub provider — same shape as existing test_integrations_api ──
class _Stub(IntegrationProvider):
    """Minimal stub satisfying the runtime-checkable Protocol."""

    kind = "sonarr"
    label = "Stub"
    config_schema: dict = {"type": "object"}
    secret_fields: tuple[str, ...] = ("api_key",)

    async def healthcheck(self, _config):
        return HealthReport(status="ok")

    async def discover_libraries(self, _config):
        return []

    async def sync_tags(self, _config):
        return []

    async def fetch_playback_events(self, _config, _since):
        return []

    async def fetch_live_playbacks(self, _config):
        return []

    async def submit_transcode_job(self, _config, _job_spec):
        from app.integrations.types import JobSubmitResult
        return JobSubmitResult(status="rejected", detail="stub")

    async def list_transcode_profiles(self, _config):
        return []

    async def get_transcode_job_status(self, _config, _upstream_job_id):
        from app.integrations.types import TranscodeJobStatus
        return TranscodeJobStatus(status="unknown")

    async def trigger_search(self, _config, _media_file_path):
        return SearchTriggerResult(status="error", detail="stub")


@pytest_asyncio.fixture
async def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[dict]:
    db_path = tmp_path / "stage7.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )

    from app.core.settings import get_settings

    get_settings.cache_clear()
    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001
    bus = get_event_bus()
    bus.clear()

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    from app.core.registry import get_registry

    registry = get_registry()
    registry.register_capability("integration.sonarr", _Stub())
    registry.register_capability("integration.radarr", _Stub())
    registry.register_capability("integration.bazarr", _Stub())

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as c:
            yield {"client": c, "db": db}
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


async def _admin_headers(client: AsyncClient) -> dict[str, str]:
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


# ── 7.1 — Path mapping discovery ─────────────────────────────────


@pytest.mark.asyncio
async def test_discover_path_mappings_matches_libraries(env, monkeypatch) -> None:
    """A Sonarr integration whose /api/v3/rootfolder lists
    ``/data/tv`` (basename "tv") should match an Auditarr
    library whose root_path ends in "tv"."""
    client = env["client"]
    db = env["db"]

    # Seed an Auditarr library + a Sonarr integration.
    async with db.session() as sess:
        sess.add(
            Library(
                id=str(uuid.uuid4()),
                name="TV",
                root_path="/mnt/media/tv",
                enabled=True,
                kind="media",
            )
        )
        integ_id = str(uuid.uuid4())
        sess.add(
            Integration(
                id=integ_id,
                name="Sonarr",
                kind="sonarr",
                enabled=True,
                config={"base_url": "http://sonarr.test"},
            )
        )
        await sess.commit()

    # Encrypt + write a fake api_key secret so build_config
    # passes the "missing api_key" guard in some providers.
    async with db.session() as sess:
        from app.security.secrets import get_secret_box
        from sqlalchemy import update as _update
        ct = get_secret_box().encrypt_dict({"api_key": "fake"})
        await sess.execute(
            _update(Integration)
            .where(Integration.id == integ_id)
            .values(secrets_ciphertext=ct)
        )
        await sess.commit()

    # Patch the discovery module's HTTP fetch to return a known
    # rootfolder shape rather than spinning up a real Sonarr.
    from app.integrations import discovery

    async def fake_fetch(_config, _base_url):
        return ["/data/tv", "/data/anime"]

    monkeypatch.setattr(
        discovery, "_fetch_arr_root_folders", fake_fetch
    )

    headers = await _admin_headers(client)
    r = await client.post(
        f"/api/v1/integrations/{integ_id}/discover-path-mappings",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "sonarr"
    suggestions = body["suggestions"]
    # /data/tv matched the library (basename "tv" == "tv"),
    # /data/anime didn't match anything → confidence "none".
    by_from = {s["from"]: s for s in suggestions}
    assert by_from["/data/tv"]["to"] == "/mnt/media/tv"
    assert by_from["/data/tv"]["confidence"] in ("high", "medium")
    assert by_from["/data/anime"]["confidence"] == "none"
    assert by_from["/data/anime"]["to"] == ""


@pytest.mark.asyncio
async def test_discover_path_mappings_returns_empty_when_no_base_url(
    env,
) -> None:
    """No base_url configured → suggestions list is empty (not
    a 500). Operator should see the empty state, not a panic."""
    client = env["client"]
    async with env["db"].session() as sess:
        integ_id = str(uuid.uuid4())
        sess.add(
            Integration(
                id=integ_id,
                name="Sonarr empty",
                kind="sonarr",
                enabled=True,
                config={},
            )
        )
        await sess.commit()

    headers = await _admin_headers(client)
    r = await client.post(
        f"/api/v1/integrations/{integ_id}/discover-path-mappings",
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["suggestions"] == []


@pytest.mark.asyncio
async def test_discover_path_mappings_unknown_integration_404(env) -> None:
    client = env["client"]
    headers = await _admin_headers(client)
    r = await client.post(
        "/api/v1/integrations/does-not-exist/discover-path-mappings",
        headers=headers,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_discover_path_mappings_requires_admin(env) -> None:
    """Discovery makes outbound HTTP — non-admin must NOT
    trigger. Mirror the existing healthcheck admin gate."""
    client = env["client"]
    # Register a non-admin user; do NOT use _admin_headers.
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "user@example.com",
            "username": "user",
            "password": PASSWORD,
        },
    )
    assert r.status_code == 201
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "user", "password": PASSWORD},
    )
    headers = {
        "authorization": f"Bearer {login.json()['access_token']}"
    }

    async with env["db"].session() as sess:
        integ_id = str(uuid.uuid4())
        sess.add(
            Integration(
                id=integ_id,
                name="Sonarr",
                kind="sonarr",
                enabled=True,
                config={"base_url": "http://sonarr.test"},
            )
        )
        await sess.commit()

    r = await client.post(
        f"/api/v1/integrations/{integ_id}/discover-path-mappings",
        headers=headers,
    )
    assert r.status_code == 403


# ── 7.1 — Webhook source discovery ───────────────────────────────


@pytest.mark.asyncio
async def test_discover_webhook_sources_aggregates_audit_log(env) -> None:
    """Sources are counted and ordered by frequency."""
    client = env["client"]
    db = env["db"]
    async with db.session() as sess:
        integ_id = str(uuid.uuid4())
        sess.add(
            Integration(
                id=integ_id,
                name="Sonarr",
                kind="sonarr",
                enabled=True,
                config={"base_url": "http://sonarr.test"},
            )
        )
        # Seed audit log entries.
        now = _dt.datetime.now(_dt.UTC)
        for ip, count in [("10.0.0.5", 3), ("10.0.0.6", 1)]:
            for _ in range(count):
                sess.add(
                    AuditLogEntry(
                        action="webhook.received",
                        actor_id=None,
                        actor_label="webhook",
                        target_type="integration",
                        target_id=integ_id,
                        metadata_={"source_ip": ip},
                        occurred_at=now,
                    )
                )
        await sess.commit()

    headers = await _admin_headers(client)
    r = await client.post(
        f"/api/v1/integrations/{integ_id}/discover-webhook-sources",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    sources = body["sources"]
    assert len(sources) == 2
    # Sorted by count desc — .5 (3 hits) first.
    assert sources[0]["ip"] == "10.0.0.5"
    assert sources[0]["count"] == 3
    assert sources[1]["ip"] == "10.0.0.6"


@pytest.mark.asyncio
async def test_discover_webhook_sources_empty_when_no_audit_rows(env) -> None:
    client = env["client"]
    async with env["db"].session() as sess:
        integ_id = str(uuid.uuid4())
        sess.add(
            Integration(
                id=integ_id,
                name="Sonarr",
                kind="sonarr",
                enabled=True,
                config={},
            )
        )
        await sess.commit()

    headers = await _admin_headers(client)
    r = await client.post(
        f"/api/v1/integrations/{integ_id}/discover-webhook-sources",
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["sources"] == []


# ── 7.2 — Tag listing endpoint ───────────────────────────────────


@pytest.mark.asyncio
async def test_upstream_tags_unsupported_kind_returns_empty(env) -> None:
    """A kind that doesn't speak tags (plex, jellyfin, tracearr)
    returns an empty list rather than 4xx — the frontend
    happily renders an empty autocomplete."""
    client = env["client"]
    async with env["db"].session() as sess:
        integ_id = str(uuid.uuid4())
        sess.add(
            Integration(
                id=integ_id,
                name="Plex",
                kind="plex",
                enabled=True,
                config={"base_url": "http://plex.test"},
            )
        )
        await sess.commit()

    headers = await _admin_headers(client)
    r = await client.get(
        f"/api/v1/integrations/{integ_id}/upstream-tags",
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["tags"] == []


@pytest.mark.asyncio
async def test_upstream_tags_returns_empty_when_no_base_url(env) -> None:
    """Sonarr integration with no base_url → empty list, not 500."""
    client = env["client"]
    async with env["db"].session() as sess:
        integ_id = str(uuid.uuid4())
        sess.add(
            Integration(
                id=integ_id,
                name="Sonarr",
                kind="sonarr",
                enabled=True,
                config={},
            )
        )
        await sess.commit()

    headers = await _admin_headers(client)
    r = await client.get(
        f"/api/v1/integrations/{integ_id}/upstream-tags",
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["tags"] == []


# ── 7.2 — Tag allowlist / denylist filtering ─────────────────────


@pytest.mark.asyncio
async def test_tag_allowlist_filters_during_sync(env) -> None:
    """Only allowlisted tags should be persisted to media_tags."""
    db = env["db"]
    async with db.session() as sess:
        lib = Library(
            id=str(uuid.uuid4()),
            name="TV",
            root_path="/mnt/media/tv",
            enabled=True,
            kind="media",
        )
        sess.add(lib)
        await sess.flush()
        mf = MediaFile(
            id=str(uuid.uuid4()),
            library_id=lib.id,
            path="/mnt/media/tv/Show/S01/ep.mkv",
            relative_path="Show/S01/ep.mkv",
            filename="ep.mkv",
            extension="mkv",
            size_bytes=1024,
            mtime=_dt.datetime.now(_dt.UTC),
            category="media",
        )
        sess.add(mf)
        integ_id = str(uuid.uuid4())
        sess.add(
            Integration(
                id=integ_id,
                name="Sonarr filtered",
                kind="sonarr",
                enabled=True,
                config={"tag_allowlist": ["keep-this"]},
            )
        )
        await sess.commit()

    # Run the apply path directly.
    async with db.session() as sess:
        from app.integrations.tag_sync import IntegrationTagSync

        integration = (
            await sess.execute(
                __import__("sqlalchemy").select(Integration).where(
                    Integration.id == integ_id
                )
            )
        ).scalar_one()
        sync = IntegrationTagSync(session=sess)
        tags = [
            TagSync(media_path="/mnt/media/tv/Show", tag="keep-this"),
            TagSync(media_path="/mnt/media/tv/Show", tag="drop-this"),
        ]
        report = await sync.apply(integration, tags)
        await sess.commit()
        assert report.inserted == 1  # only keep-this
        rows = (
            (
                await sess.execute(
                    __import__("sqlalchemy").select(MediaTag).where(
                        MediaTag.source == "sonarr"
                    )
                )
            )
            .scalars()
            .all()
        )
        names = {r.name for r in rows}
        assert names == {"keep-this"}


@pytest.mark.asyncio
async def test_tag_denylist_overrides_allowlist(env) -> None:
    """A tag in both allowlist and denylist is rejected (deny
    wins). Operators read "allowed minus denied" naturally."""
    db = env["db"]
    async with db.session() as sess:
        lib = Library(
            id=str(uuid.uuid4()),
            name="TV",
            root_path="/mnt/media/tv",
            enabled=True,
            kind="media",
        )
        sess.add(lib)
        await sess.flush()
        sess.add(
            MediaFile(
                id=str(uuid.uuid4()),
                library_id=lib.id,
                path="/mnt/media/tv/X.mkv",
                relative_path="X.mkv",
                filename="X.mkv",
                extension="mkv",
                size_bytes=1024,
                mtime=_dt.datetime.now(_dt.UTC),
                category="media",
            )
        )
        integ_id = str(uuid.uuid4())
        sess.add(
            Integration(
                id=integ_id,
                name="Sonarr deny",
                kind="sonarr",
                enabled=True,
                config={
                    "tag_allowlist": ["a", "b"],
                    "tag_denylist": ["b"],
                },
            )
        )
        await sess.commit()

    async with db.session() as sess:
        from app.integrations.tag_sync import IntegrationTagSync

        integration = (
            await sess.execute(
                __import__("sqlalchemy").select(Integration).where(
                    Integration.id == integ_id
                )
            )
        ).scalar_one()
        sync = IntegrationTagSync(session=sess)
        tags = [
            TagSync(media_path="/mnt/media/tv", tag="a"),
            TagSync(media_path="/mnt/media/tv", tag="b"),
        ]
        await sync.apply(integration, tags)
        await sess.commit()
        rows = (
            (
                await sess.execute(
                    __import__("sqlalchemy").select(MediaTag).where(
                        MediaTag.source == "sonarr"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {r.name for r in rows} == {"a"}


@pytest.mark.asyncio
async def test_tag_filter_is_case_insensitive(env) -> None:
    """Operators don't think in case. KEEP / keep / Keep all
    match."""
    db = env["db"]
    async with db.session() as sess:
        lib = Library(
            id=str(uuid.uuid4()),
            name="TV",
            root_path="/mnt/media/tv",
            enabled=True,
            kind="media",
        )
        sess.add(lib)
        await sess.flush()
        sess.add(
            MediaFile(
                id=str(uuid.uuid4()),
                library_id=lib.id,
                path="/mnt/media/tv/X.mkv",
                relative_path="X.mkv",
                filename="X.mkv",
                extension="mkv",
                size_bytes=1024,
                mtime=_dt.datetime.now(_dt.UTC),
                category="media",
            )
        )
        integ_id = str(uuid.uuid4())
        sess.add(
            Integration(
                id=integ_id,
                name="Sonarr case",
                kind="sonarr",
                enabled=True,
                config={"tag_allowlist": ["KEEP"]},
            )
        )
        await sess.commit()

    async with db.session() as sess:
        from app.integrations.tag_sync import IntegrationTagSync

        integration = (
            await sess.execute(
                __import__("sqlalchemy").select(Integration).where(
                    Integration.id == integ_id
                )
            )
        ).scalar_one()
        sync = IntegrationTagSync(session=sess)
        await sync.apply(
            integration,
            [TagSync(media_path="/mnt/media/tv", tag="keep")],
        )
        await sess.commit()
        rows = (
            (
                await sess.execute(
                    __import__("sqlalchemy").select(MediaTag).where(
                        MediaTag.source == "sonarr"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {r.name for r in rows} == {"keep"}


@pytest.mark.asyncio
async def test_flipping_allow_to_deny_removes_previously_synced_tags(
    env,
) -> None:
    """After a tag was synced under allow, then the operator
    moves it to denylist + runs another sync, the corresponding
    MediaTag rows are deleted. The "desired set" computation
    handles this naturally."""
    db = env["db"]
    async with db.session() as sess:
        lib = Library(
            id=str(uuid.uuid4()),
            name="TV",
            root_path="/mnt/media/tv",
            enabled=True,
            kind="media",
        )
        sess.add(lib)
        await sess.flush()
        sess.add(
            MediaFile(
                id=str(uuid.uuid4()),
                library_id=lib.id,
                path="/mnt/media/tv/X.mkv",
                relative_path="X.mkv",
                filename="X.mkv",
                extension="mkv",
                size_bytes=1024,
                mtime=_dt.datetime.now(_dt.UTC),
                category="media",
            )
        )
        integ_id = str(uuid.uuid4())
        sess.add(
            Integration(
                id=integ_id,
                name="Sonarr flip",
                kind="sonarr",
                enabled=True,
                config={"tag_allowlist": ["x"]},
            )
        )
        await sess.commit()

    # First sync: tag "x" is allowed.
    async with db.session() as sess:
        from app.integrations.tag_sync import IntegrationTagSync

        integration = (
            await sess.execute(
                __import__("sqlalchemy").select(Integration).where(
                    Integration.id == integ_id
                )
            )
        ).scalar_one()
        sync = IntegrationTagSync(session=sess)
        await sync.apply(
            integration, [TagSync(media_path="/mnt/media/tv", tag="x")]
        )
        await sess.commit()

    # Flip "x" to denylist; re-sync (provider keeps emitting it).
    async with db.session() as sess:
        await sess.execute(
            __import__("sqlalchemy")
            .update(Integration)
            .where(Integration.id == integ_id)
            .values(config={"tag_denylist": ["x"]})
        )
        await sess.commit()
    async with db.session() as sess:
        from app.integrations.tag_sync import IntegrationTagSync

        integration = (
            await sess.execute(
                __import__("sqlalchemy").select(Integration).where(
                    Integration.id == integ_id
                )
            )
        ).scalar_one()
        sync = IntegrationTagSync(session=sess)
        report = await sync.apply(
            integration, [TagSync(media_path="/mnt/media/tv", tag="x")]
        )
        await sess.commit()
        assert report.removed >= 1
        rows = (
            (
                await sess.execute(
                    __import__("sqlalchemy").select(MediaTag).where(
                        MediaTag.source == "sonarr"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows == []
