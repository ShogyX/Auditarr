"""Stage 26 — Dashboard categories endpoint.

Pins the new ``GET /api/v1/dashboard/categories`` contract:

  - returns grouped composition (video_codec + container)
  - rows sorted by total_size_bytes descending within each group
  - NULL codecs/containers collapse to a single ``unknown`` row per group
  - ``limit`` parameter caps results per group
  - non-admin authenticated users can read it (operators audit
    composition; this isn't admin-only sensitive data)

The structural fixture (one library, three files) is intentionally
small — the existing dashboard test suite already exercises the
larger aggregation surface; this file only covers what's new in
Stage 26.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.library import Library
from app.models.media import MediaFile
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database
from app.utils.datetime import utcnow

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "dashboard_stage26.db"
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


async def _user_headers(client: AsyncClient) -> dict[str, str]:
    """Auth as a regular (non-admin) user. The categories endpoint is
    readable by any authenticated user — auditing composition isn't a
    privileged action."""
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


async def _seed_library() -> None:
    """Seed three media files with varied codecs / containers / sizes.

    Sizes are chosen so the size-descending ordering is unambiguous:
    hevc (largest) > h264 > av1 within the video_codec group.
    """
    now = utcnow()
    async with get_database().session() as sess:
        lib = Library(name="Movies", root_path="/data", kind="movies")
        sess.add(lib)
        await sess.flush()

        sess.add_all(
            [
                MediaFile(
                    library_id=lib.id,
                    path="/data/a.mkv",
                    relative_path="a.mkv",
                    filename="a.mkv",
                    extension="mkv",
                    size_bytes=10 * 1024 * 1024 * 1024,  # 10 GiB
                    mtime=now,
                    category="media",
                    severity="ok",
                    severity_rank=10,
                    video_codec="hevc",
                    container="matroska",
                    has_subtitles=False,
                    seen_at=now,
                    is_orphaned=False,
                ),
                MediaFile(
                    library_id=lib.id,
                    path="/data/b.mp4",
                    relative_path="b.mp4",
                    filename="b.mp4",
                    extension="mp4",
                    size_bytes=5 * 1024 * 1024 * 1024,  # 5 GiB
                    mtime=now,
                    category="media",
                    severity="ok",
                    severity_rank=10,
                    video_codec="h264",
                    container="mp4",
                    has_subtitles=False,
                    seen_at=now,
                    is_orphaned=False,
                ),
                MediaFile(
                    library_id=lib.id,
                    path="/data/c.mkv",
                    relative_path="c.mkv",
                    filename="c.mkv",
                    extension="mkv",
                    size_bytes=1 * 1024 * 1024 * 1024,  # 1 GiB
                    mtime=now,
                    category="media",
                    severity="info",
                    severity_rank=20,
                    video_codec="av1",
                    container=None,  # unprobed → unknown
                    has_subtitles=False,
                    seen_at=now,
                    is_orphaned=False,
                ),
            ]
        )
        await sess.commit()


# ── Tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_categories_returns_video_codec_and_container_groups(
    client: AsyncClient,
) -> None:
    await _seed_library()
    headers = await _user_headers(client)

    response = await client.get("/api/v1/dashboard/categories", headers=headers)
    assert response.status_code == 200, response.text
    rows = response.json()

    groups = {row["group"] for row in rows}
    assert groups == {"video_codec", "container"}


@pytest.mark.asyncio
async def test_categories_video_codec_sorted_by_size_desc(
    client: AsyncClient,
) -> None:
    await _seed_library()
    headers = await _user_headers(client)

    response = await client.get("/api/v1/dashboard/categories", headers=headers)
    rows = response.json()
    video = [r for r in rows if r["group"] == "video_codec"]

    # Order: hevc (10G) > h264 (5G) > av1 (1G)
    keys = [r["key"] for r in video]
    assert keys == ["hevc", "h264", "av1"]
    # Sizes are returned as integers, not abbreviated strings
    assert video[0]["total_size_bytes"] == 10 * 1024 * 1024 * 1024
    assert video[0]["file_count"] == 1


@pytest.mark.asyncio
async def test_categories_collapses_null_container_to_unknown(
    client: AsyncClient,
) -> None:
    await _seed_library()
    headers = await _user_headers(client)

    response = await client.get("/api/v1/dashboard/categories", headers=headers)
    rows = response.json()
    containers = [r for r in rows if r["group"] == "container"]
    keys = {r["key"] for r in containers}

    # One file had container=None → should appear as ``unknown``.
    assert "unknown" in keys
    # Real containers present too.
    assert "matroska" in keys
    assert "mp4" in keys


@pytest.mark.asyncio
async def test_categories_limit_caps_results_per_group(
    client: AsyncClient,
) -> None:
    await _seed_library()
    headers = await _user_headers(client)

    response = await client.get(
        "/api/v1/dashboard/categories?limit=2", headers=headers
    )
    rows = response.json()
    video = [r for r in rows if r["group"] == "video_codec"]
    container = [r for r in rows if r["group"] == "container"]

    # ``limit`` caps each group independently. With 3 codecs and 3
    # containers and limit=2, we get 2 per group.
    assert len(video) == 2
    assert len(container) == 2


@pytest.mark.asyncio
async def test_categories_empty_library_returns_empty_list(
    client: AsyncClient,
) -> None:
    # No files seeded — endpoint should return [] without crashing.
    headers = await _user_headers(client)

    response = await client.get("/api/v1/dashboard/categories", headers=headers)
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_categories_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/api/v1/dashboard/categories")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_categories_non_admin_can_read(client: AsyncClient) -> None:
    """Composition isn't admin-only. Confirmed explicitly because the
    audit team will want to see codec breakdowns without elevated rights."""
    await _seed_library()
    headers = await _user_headers(client)

    response = await client.get("/api/v1/dashboard/categories", headers=headers)
    assert response.status_code == 200
