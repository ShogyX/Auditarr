"""Optimization API integration tests.

We test profile CRUD, the manual enqueue endpoint, retry/cancel state
transitions, and the run-now endpoints (which dispatch to the worker —
the worker itself has its own dedicated tests).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.library import Library
from app.models.media import MediaFile
from app.models.optimization import OptimizationItem
from app.models.user import User
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database
from app.utils.datetime import utcnow

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "opt_api.db"
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

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
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
        get_settings.cache_clear()


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


async def _seed_media() -> str:
    """Insert library + file. Returns media_file_id."""
    async with get_database().session() as sess:
        lib = Library(name="Movies", root_path="/data/movies", kind="movies")
        sess.add(lib)
        await sess.flush()
        media = MediaFile(
            library_id=lib.id,
            path="/data/movies/x.mkv",
            relative_path="x.mkv",
            filename="x.mkv",
            extension="mkv",
            size_bytes=1_000_000,
            mtime=utcnow(),
            category="media",
            severity="ok",
            severity_rank=10,
            seen_at=utcnow(),
            is_orphaned=False,
            has_subtitles=False,
        )
        sess.add(media)
        await sess.commit()
        return media.id


# ── Profiles ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_profile_crud(client: AsyncClient) -> None:
    headers = await _admin_headers(client)

    create = await client.post(
        "/api/v1/optimization/profiles",
        headers=headers,
        json={
            "name": "Shrink HEVC",
            "description": "Reduce big HEVC files",
            "settings": {
                "video": {"codec": "libx265", "crf": 22, "preset": "medium"},
                "audio": {"codec": "copy"},
                "output": {"container": "mkv"},
            },
        },
    )
    assert create.status_code == 201, create.text
    profile_id = create.json()["id"]

    listing = await client.get(
        "/api/v1/optimization/profiles", headers=headers
    )
    assert {p["id"] for p in listing.json()} == {profile_id}

    patch = await client.patch(
        f"/api/v1/optimization/profiles/{profile_id}",
        headers=headers,
        json={"enabled": False},
    )
    assert patch.json()["enabled"] is False

    delete = await client.delete(
        f"/api/v1/optimization/profiles/{profile_id}", headers=headers
    )
    assert delete.status_code == 204


@pytest.mark.asyncio
async def test_profile_rejects_unsupported_codec(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/optimization/profiles",
        headers=headers,
        json={
            "name": "Broken",
            "settings": {"video": {"codec": "rot13"}},
        },
    )
    assert response.status_code == 422


# ── Enqueue + queue ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_enqueue_creates_queued_item(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    media_id = await _seed_media()

    # Create a profile to point at.
    await client.post(
        "/api/v1/optimization/profiles",
        headers=headers,
        json={"name": "Shrink", "settings": {}},
    )

    response = await client.post(
        "/api/v1/optimization/enqueue",
        headers=headers,
        json={"media_file_id": media_id, "profile": "Shrink"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["profile"] == "Shrink"
    assert body["progress_pct"] == 0


@pytest.mark.asyncio
async def test_enqueue_rejects_unknown_profile(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    media_id = await _seed_media()

    response = await client.post(
        "/api/v1/optimization/enqueue",
        headers=headers,
        json={"media_file_id": media_id, "profile": "ghost"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_enqueue_rejects_unknown_media_file(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    await client.post(
        "/api/v1/optimization/profiles",
        headers=headers,
        json={"name": "Shrink", "settings": {}},
    )

    response = await client.post(
        "/api/v1/optimization/enqueue",
        headers=headers,
        json={"media_file_id": "no-such-file", "profile": "Shrink"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_queue_listing_filters_by_status(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    media_id = await _seed_media()
    await client.post(
        "/api/v1/optimization/profiles",
        headers=headers,
        json={"name": "Shrink", "settings": {}},
    )
    await client.post(
        "/api/v1/optimization/enqueue",
        headers=headers,
        json={"media_file_id": media_id, "profile": "Shrink"},
    )
    queued = await client.get(
        "/api/v1/optimization/queue?status=queued", headers=headers
    )
    assert len(queued.json()) == 1
    completed = await client.get(
        "/api/v1/optimization/queue?status=completed", headers=headers
    )
    assert completed.json() == []


# ── Cancel / retry ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cancel_queued_item(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    media_id = await _seed_media()
    await client.post(
        "/api/v1/optimization/profiles",
        headers=headers,
        json={"name": "Shrink", "settings": {}},
    )
    enqueue = await client.post(
        "/api/v1/optimization/enqueue",
        headers=headers,
        json={"media_file_id": media_id, "profile": "Shrink"},
    )
    item_id = enqueue.json()["id"]

    cancel = await client.post(
        f"/api/v1/optimization/{item_id}/cancel", headers=headers
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"

    # Cancelling again is a 422 (terminal state).
    again = await client.post(
        f"/api/v1/optimization/{item_id}/cancel", headers=headers
    )
    assert again.status_code == 422


@pytest.mark.asyncio
async def test_retry_failed_item(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    media_id = await _seed_media()
    await client.post(
        "/api/v1/optimization/profiles",
        headers=headers,
        json={"name": "Shrink", "settings": {}},
    )
    enqueue = await client.post(
        "/api/v1/optimization/enqueue",
        headers=headers,
        json={"media_file_id": media_id, "profile": "Shrink"},
    )
    item_id = enqueue.json()["id"]

    # Manually flip to failed so the retry endpoint has something to re-queue.
    async with get_database().session() as sess:
        item = await sess.get(OptimizationItem, item_id)
        assert item is not None
        item.status = "failed"
        item.error = "synthetic"
        item.progress_pct = 42
        await sess.commit()

    retry = await client.post(
        f"/api/v1/optimization/{item_id}/retry", headers=headers
    )
    assert retry.status_code == 200
    body = retry.json()
    assert body["status"] == "queued"
    assert body["progress_pct"] == 0
    assert body["error"] is None


# ── Worker dispatch through API ────────────────────────────────
@pytest.mark.asyncio
async def test_run_next_with_empty_queue_is_idle(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/optimization/run-next", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["status"] == "idle"


@pytest.mark.asyncio
async def test_run_item_404_for_unknown(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/optimization/no-such-item/run", headers=headers
    )
    assert response.status_code == 404
