"""Tag management API — summary + bulk-delete.

Pins the contract behind the Settings → Tags surface:

  1. ``GET /tags/summary`` groups by (name, source) and counts files.
  2. ``POST /tags/delete`` requires at least one filter.
  3. Deletion by name removes the tag across every file regardless of
     source.
  4. Deletion by source removes only that origin's tags.
  5. Deletion by (name, source) is the most precise scope.
  6. Non-admin users get 403 on the delete path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.models.library import Library
from app.models.media import MediaFile
from app.models.tag import MediaTag
from app.models.user import User
from app.storage.base import Base
from app.storage.database import get_database
from app.utils.datetime import utcnow

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "tags.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    db = get_database()
    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await db.disconnect()


async def _admin(client: AsyncClient) -> dict[str, str]:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "admin@example.com",
            "username": "admin",
            "password": PASSWORD,
        },
    )
    # Promote to admin directly via the DB — registration creates a
    # ``viewer`` by default.
    db = get_database()
    async with db.session() as sess:
        from sqlalchemy import update

        await sess.execute(update(User).values(role="admin"))
        await sess.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "admin", "password": PASSWORD},
    )
    return {"authorization": f"Bearer {login.json()['access_token']}"}


async def _viewer(client: AsyncClient) -> dict[str, str]:
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


async def _seed_tags() -> None:
    """Insert two libraries, three files, six tags spanning two sources."""
    db = get_database()
    async with db.session() as sess:
        lib = Library(name="Movies", root_path="/m", kind="movies")
        sess.add(lib)
        await sess.flush()
        files = []
        for i in range(3):
            mf = MediaFile(
                library_id=lib.id,
                path=f"/m/f{i}.mkv",
                relative_path=f"f{i}.mkv",
                filename=f"f{i}.mkv",
                extension="mkv",
                size_bytes=1000,
                mtime=utcnow(),
                category="media",
                severity="ok",
                severity_rank=10,
                seen_at=utcnow(),
                is_orphaned=False,
            )
            sess.add(mf)
            await sess.flush()
            files.append(mf)
        # f0: 4k from sonarr + 4k from manual (different sources, same name)
        sess.add_all([
            MediaTag(media_file_id=files[0].id, name="4k", source="sonarr"),
            MediaTag(media_file_id=files[0].id, name="4k", source="manual"),
            # f1: 4k from sonarr only
            MediaTag(media_file_id=files[1].id, name="4k", source="sonarr"),
            # f2: missing-subs:fr from bazarr + 1080p from radarr
            MediaTag(media_file_id=files[2].id, name="missing-subs:fr", source="bazarr"),
            MediaTag(media_file_id=files[2].id, name="1080p", source="radarr"),
        ])
        await sess.commit()


@pytest.mark.asyncio
async def test_summary_groups_by_name_and_source(client: AsyncClient) -> None:
    await _seed_tags()
    headers = await _admin(client)
    r = await client.get("/api/v1/tags/summary", headers=headers)
    assert r.status_code == 200
    rows = {(row["name"], row["source"]): row["file_count"] for row in r.json()}
    assert rows == {
        ("1080p", "radarr"): 1,
        ("4k", "manual"): 1,
        ("4k", "sonarr"): 2,
        ("missing-subs:fr", "bazarr"): 1,
    }


@pytest.mark.asyncio
async def test_delete_requires_at_least_one_filter(client: AsyncClient) -> None:
    headers = await _admin(client)
    r = await client.post("/api/v1/tags/delete", headers=headers, json={})
    assert r.status_code == 422
    body = r.json()
    assert "name" in body["message"].lower() or "source" in body["message"].lower()


@pytest.mark.asyncio
async def test_delete_by_name_drops_across_sources(client: AsyncClient) -> None:
    await _seed_tags()
    headers = await _admin(client)
    r = await client.post(
        "/api/v1/tags/delete",
        headers=headers,
        json={"name": "4k"},
    )
    assert r.status_code == 200
    # Three rows had name=4k (two sonarr, one manual).
    assert r.json()["deleted"] == 3

    summary = (await client.get("/api/v1/tags/summary", headers=headers)).json()
    # Only the radarr 1080p + bazarr missing-subs:fr remain.
    assert {(row["name"], row["source"]) for row in summary} == {
        ("1080p", "radarr"),
        ("missing-subs:fr", "bazarr"),
    }


@pytest.mark.asyncio
async def test_delete_by_source_drops_only_that_origin(client: AsyncClient) -> None:
    await _seed_tags()
    headers = await _admin(client)
    r = await client.post(
        "/api/v1/tags/delete",
        headers=headers,
        json={"source": "sonarr"},
    )
    assert r.status_code == 200
    assert r.json()["deleted"] == 2  # f0 + f1 sonarr 4k rows

    summary = (await client.get("/api/v1/tags/summary", headers=headers)).json()
    # f0's manual 4k survives; bazarr + radarr untouched.
    assert {(row["name"], row["source"]) for row in summary} == {
        ("1080p", "radarr"),
        ("4k", "manual"),
        ("missing-subs:fr", "bazarr"),
    }


@pytest.mark.asyncio
async def test_delete_by_name_and_source_is_most_precise(client: AsyncClient) -> None:
    await _seed_tags()
    headers = await _admin(client)
    r = await client.post(
        "/api/v1/tags/delete",
        headers=headers,
        json={"name": "4k", "source": "manual"},
    )
    assert r.status_code == 200
    assert r.json()["deleted"] == 1


@pytest.mark.asyncio
async def test_delete_rejects_non_admin(client: AsyncClient) -> None:
    await _seed_tags()
    headers = await _viewer(client)
    r = await client.post(
        "/api/v1/tags/delete",
        headers=headers,
        json={"name": "4k"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_summary_open_to_all_users(client: AsyncClient) -> None:
    await _seed_tags()
    headers = await _viewer(client)
    r = await client.get("/api/v1/tags/summary", headers=headers)
    assert r.status_code == 200
    assert len(r.json()) == 4
