"""Stage 28 — Bulk-enqueue optimization endpoint.

Pins the contract of ``POST /api/v1/optimization/bulk-enqueue``:

  - admin-only (selection bar exposes it to operators with the
    elevated role)
  - resolves profile by name; whole request fails if profile is
    missing OR disabled
  - per-bucket outcome counts: queued / already_queued /
    skipped_active / files_not_found
  - duplicate media_ids are a 400
  - existing (file, profile) entries in terminal states are
    NOT clobbered

The Stage 23 ``BulkReevaluate`` conventions (admin-only, ≤500
ids, reject duplicates) are reused — every bulk endpoint in the
project should feel identical to the operator.
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
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "opt_stage28.db"
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


async def _user_headers(client: AsyncClient) -> dict[str, str]:
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
    return {"authorization": f"Bearer {login.json()['access_token']}"}


async def _seed_files(n: int = 3) -> list[str]:
    """Insert library + N media files. Returns the media ids."""
    ids: list[str] = []
    async with get_database().session() as sess:
        lib = Library(name="Movies", root_path="/data/movies", kind="movies")
        sess.add(lib)
        await sess.flush()
        for i in range(n):
            mf = MediaFile(
                library_id=lib.id,
                path=f"/data/movies/x{i}.mkv",
                relative_path=f"x{i}.mkv",
                filename=f"x{i}.mkv",
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
            sess.add(mf)
            await sess.flush()
            ids.append(mf.id)
        await sess.commit()
    return ids


async def _create_profile(
    client: AsyncClient,
    headers: dict[str, str],
    name: str = "Shrink HEVC",
    enabled: bool = True,
) -> str:
    response = await client.post(
        "/api/v1/optimization/profiles",
        headers=headers,
        json={
            "name": name,
            "enabled": enabled,
            "settings": {
                "video": {"codec": "libx265", "crf": 22, "preset": "medium"},
                "audio": {"codec": "copy"},
                "output": {"container": "mkv"},
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


# ── Happy path ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_enqueue_queues_new_files(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    media_ids = await _seed_files(3)
    await _create_profile(client, headers)

    response = await client.post(
        "/api/v1/optimization/bulk-enqueue",
        headers=headers,
        json={"media_ids": media_ids, "profile": "Shrink HEVC"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["queued"] == 3
    assert body["already_queued"] == 0
    assert body["skipped_active"] == 0
    assert body["files_not_found"] == []


@pytest.mark.asyncio
async def test_bulk_enqueue_idempotent_for_already_queued(
    client: AsyncClient,
) -> None:
    """Re-issuing the same bulk request reports ``already_queued``
    rather than adding duplicates."""
    headers = await _admin_headers(client)
    media_ids = await _seed_files(2)
    await _create_profile(client, headers)

    # First call queues both.
    first = await client.post(
        "/api/v1/optimization/bulk-enqueue",
        headers=headers,
        json={"media_ids": media_ids, "profile": "Shrink HEVC"},
    )
    assert first.json()["queued"] == 2

    # Second call sees them as already-queued.
    second = await client.post(
        "/api/v1/optimization/bulk-enqueue",
        headers=headers,
        json={"media_ids": media_ids, "profile": "Shrink HEVC"},
    )
    assert second.status_code == 200
    body = second.json()
    assert body["queued"] == 0
    assert body["already_queued"] == 2

    # Confirm no duplicate rows landed.
    async with get_database().session() as sess:
        from sqlalchemy import select, func

        count = (
            await sess.execute(select(func.count(OptimizationItem.id)))
        ).scalar_one()
        assert count == 2


@pytest.mark.asyncio
async def test_bulk_enqueue_skips_active_items(client: AsyncClient) -> None:
    """If (file, profile) is in running / completed / failed /
    cancelled / skipped, the bulk endpoint leaves it alone — the
    operator must use Retry to re-queue."""
    headers = await _admin_headers(client)
    [media_id] = await _seed_files(1)
    await _create_profile(client, headers)

    # Seed a "completed" item for this (file, profile).
    async with get_database().session() as sess:
        item = OptimizationItem(
            media_file_id=media_id,
            profile="Shrink HEVC",
            status="completed",
            queued_at=utcnow(),
            finished_at=utcnow(),
            item_metadata={},
        )
        sess.add(item)
        await sess.commit()

    response = await client.post(
        "/api/v1/optimization/bulk-enqueue",
        headers=headers,
        json={"media_ids": [media_id], "profile": "Shrink HEVC"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["queued"] == 0
    assert body["already_queued"] == 0
    assert body["skipped_active"] == 1


@pytest.mark.asyncio
async def test_bulk_enqueue_partial_unknown_ids(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    media_ids = await _seed_files(2)
    await _create_profile(client, headers)

    response = await client.post(
        "/api/v1/optimization/bulk-enqueue",
        headers=headers,
        json={
            "media_ids": [*media_ids, "missing-id"],
            "profile": "Shrink HEVC",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["queued"] == 2
    assert body["files_not_found"] == ["missing-id"]


# ── Failure modes ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_enqueue_unknown_profile_404(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    media_ids = await _seed_files(1)

    response = await client.post(
        "/api/v1/optimization/bulk-enqueue",
        headers=headers,
        json={"media_ids": media_ids, "profile": "Nope"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_bulk_enqueue_disabled_profile_rejected(
    client: AsyncClient,
) -> None:
    """Disabled profiles are explicitly rejected — a disabled
    profile won't actually run, so silently enqueueing against it
    would build up a stale backlog."""
    headers = await _admin_headers(client)
    media_ids = await _seed_files(1)
    await _create_profile(client, headers, name="Off", enabled=False)

    response = await client.post(
        "/api/v1/optimization/bulk-enqueue",
        headers=headers,
        json={"media_ids": media_ids, "profile": "Off"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_bulk_enqueue_admin_only(client: AsyncClient) -> None:
    media_ids = await _seed_files(1)
    admin_h = await _admin_headers(client)
    await _create_profile(client, admin_h)

    user_h = await _user_headers(client)
    response = await client.post(
        "/api/v1/optimization/bulk-enqueue",
        headers=user_h,
        json={"media_ids": media_ids, "profile": "Shrink HEVC"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_bulk_enqueue_rejects_duplicate_ids(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    media_ids = await _seed_files(1)
    await _create_profile(client, headers)

    response = await client.post(
        "/api/v1/optimization/bulk-enqueue",
        headers=headers,
        json={
            "media_ids": [media_ids[0], media_ids[0]],
            "profile": "Shrink HEVC",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_bulk_enqueue_empty_list_422(client: AsyncClient) -> None:
    """The schema constrains ``min_length=1``; an empty list is a
    422 validation failure rather than a no-op success."""
    headers = await _admin_headers(client)
    await _create_profile(client, headers)

    response = await client.post(
        "/api/v1/optimization/bulk-enqueue",
        headers=headers,
        json={"media_ids": [], "profile": "Shrink HEVC"},
    )
    assert response.status_code == 422
