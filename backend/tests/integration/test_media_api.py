"""Library and scan API integration tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.user import User
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def media_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "media.db"
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


def _seed_library(root: Path) -> None:
    sub = root / "Movies" / "Sample (2024)"
    sub.mkdir(parents=True)
    (sub / "movie.mkv").write_bytes(b"x" * 200)
    (sub / "movie.eng.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n")
    (sub / "poster.jpg").write_bytes(b"\xff\xd8\xff")
    (sub / ".DS_Store").write_bytes(b"x")


@pytest.mark.asyncio
async def test_library_crud(media_client: AsyncClient, tmp_path: Path) -> None:
    headers = await _admin_headers(media_client)
    root = tmp_path / "lib"
    root.mkdir()

    create = await media_client.post(
        "/api/v1/libraries",
        headers=headers,
        json={"name": "Movies", "root_path": str(root), "kind": "movies"},
    )
    assert create.status_code == 201, create.text
    library_id = create.json()["id"]

    listing = await media_client.get("/api/v1/libraries", headers=headers)
    assert listing.status_code == 200
    assert {lib["id"] for lib in listing.json()} == {library_id}

    update_res = await media_client.patch(
        f"/api/v1/libraries/{library_id}",
        headers=headers,
        json={"scan_interval_minutes": 60},
    )
    assert update_res.status_code == 200
    assert update_res.json()["scan_interval_minutes"] == 60

    delete = await media_client.delete(
        f"/api/v1/libraries/{library_id}", headers=headers
    )
    assert delete.status_code == 204

    after = await media_client.get("/api/v1/libraries", headers=headers)
    assert after.json() == []


@pytest.mark.asyncio
async def test_non_admin_cannot_create_library(
    media_client: AsyncClient, tmp_path: Path
) -> None:
    # Register a non-admin user.
    await media_client.post(
        "/api/v1/auth/register",
        json={
            "email": "u@example.com",
            "username": "user1",
            "password": PASSWORD,
        },
    )
    login = await media_client.post(
        "/api/v1/auth/login",
        json={"login": "user1", "password": PASSWORD},
    )
    headers = {"authorization": f"Bearer {login.json()['access_token']}"}
    response = await media_client.post(
        "/api/v1/libraries",
        headers=headers,
        json={"name": "x", "root_path": str(tmp_path)},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_trigger_scan_and_list_media(
    media_client: AsyncClient, tmp_path: Path
) -> None:
    headers = await _admin_headers(media_client)
    root = tmp_path / "lib"
    root.mkdir()
    _seed_library(root)

    create = await media_client.post(
        "/api/v1/libraries",
        headers=headers,
        json={"name": "Movies", "root_path": str(root), "kind": "movies"},
    )
    library_id = create.json()["id"]

    scan = await media_client.post(
        # Stage 8 (audit follow-up): scan API default flipped to
        # async. Tests that need a sync-and-seeded scan must
        # explicitly request ``?enqueue=false`` so the response is
        # the completed run (with files_seen, etc.) rather than a
        # ``queued`` placeholder.
        f"/api/v1/scans/libraries/{library_id}?enqueue=false",
        headers=headers,
        json={"mode": "full"},
    )
    assert scan.status_code == 202, scan.text
    body = scan.json()
    assert body["status"] == "completed"
    assert body["files_seen"] == 4

    media = await media_client.get(
        f"/api/v1/media?library_id={library_id}", headers=headers
    )
    assert media.status_code == 200
    payload = media.json()
    assert payload["total"] == 4
    categories = {item["category"] for item in payload["items"]}
    assert categories == {"media", "subtitle", "image", "junk"}


@pytest.mark.asyncio
async def test_media_filter_by_category(
    media_client: AsyncClient, tmp_path: Path
) -> None:
    headers = await _admin_headers(media_client)
    root = tmp_path / "lib"
    root.mkdir()
    _seed_library(root)
    create = await media_client.post(
        "/api/v1/libraries",
        headers=headers,
        json={"name": "Movies", "root_path": str(root)},
    )
    library_id = create.json()["id"]
    await media_client.post(
        # Stage 8 (audit follow-up): sync mode required to seed
        # category data before the assertion below.
        f"/api/v1/scans/libraries/{library_id}?enqueue=false",
        headers=headers,
        json={"mode": "full"},
    )

    # Junk should be filterable as a category — useful for cleanup workflows.
    junk = await media_client.get("/api/v1/media?category=junk", headers=headers)
    assert junk.status_code == 200
    assert junk.json()["total"] == 1
    assert junk.json()["items"][0]["filename"] == ".DS_Store"


@pytest.mark.asyncio
async def test_scan_unknown_library_404(media_client: AsyncClient) -> None:
    headers = await _admin_headers(media_client)
    response = await media_client.post(
        "/api/v1/scans/libraries/does-not-exist",
        headers=headers,
        json={"mode": "full"},
    )
    assert response.status_code == 404
