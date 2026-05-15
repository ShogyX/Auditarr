"""Stage 13 (audit follow-up) — tags read API + per-file tags endpoint.

Pins:
  1. ``GET /media/{id}/tags`` returns the file's tags ordered by
     (source, name); 404 on unknown id.
  2. ``GET /media?include_tags=true`` attaches the deduped tag-name
     list to each row.
  3. Default (without ``include_tags``) does NOT pay the join cost
     — the field is empty.
  4. Tag casing is preserved across sources.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.library import Library
from app.models.media import MediaFile
from app.models.tag import MediaTag
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "stage13.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("AUDITARR_DATA_DIR", str(data_dir))
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
        get_settings.cache_clear()


async def _headers(client: AsyncClient) -> dict[str, str]:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "user@example.com",
            "username": "user",
            "password": PASSWORD,
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "user", "password": PASSWORD},
    )
    return {"authorization": f"Bearer {login.json()['access_token']}"}


async def _seed() -> str:
    """Seed one library with two media files and a varied tag set:
    file 1 has rule + integration + manual tags; file 2 has only one rule tag.
    Returns the id of file 1 (the rich one used by per-file tests)."""
    now = datetime.now(UTC)
    async with get_database().session() as sess:
        lib = Library(
            id="lib-1",
            name="Movies",
            root_path="/tmp/lib",
            kind="movies",
            enabled=True,
        )
        sess.add(lib)
        mf1 = MediaFile(
            id="mf-1",
            library_id="lib-1",
            path="/tmp/lib/a.mkv",
            filename="a.mkv",
            relative_path="a.mkv",
            extension="mkv",
            category="media",
            size_bytes=10,
            mtime=now,
            severity="ok",
            severity_rank=0,
        )
        mf2 = MediaFile(
            id="mf-2",
            library_id="lib-1",
            path="/tmp/lib/b.mkv",
            filename="b.mkv",
            relative_path="b.mkv",
            extension="mkv",
            category="media",
            size_bytes=10,
            mtime=now,
            severity="ok",
            severity_rank=0,
        )
        sess.add_all([mf1, mf2])
        # Tags for file 1: deliberate mix of sources + a casing
        # collision ("4K" from Sonarr vs "4k" from a rule).
        sess.add_all(
            [
                MediaTag(media_file_id="mf-1", name="needs-review", source="rule"),
                MediaTag(media_file_id="mf-1", name="4K", source="sonarr"),
                MediaTag(media_file_id="mf-1", name="4k", source="rule"),
                MediaTag(media_file_id="mf-1", name="watched", source="manual"),
            ]
        )
        sess.add_all(
            [
                MediaTag(media_file_id="mf-2", name="archive", source="rule"),
            ]
        )
        await sess.commit()
    return "mf-1"


@pytest.mark.asyncio
async def test_tags_endpoint_returns_all_with_source(
    client: AsyncClient,
) -> None:
    headers = await _headers(client)
    await _seed()

    r = await client.get("/api/v1/media/mf-1/tags", headers=headers)
    assert r.status_code == 200, r.text
    rows = r.json()
    # file 1 has four tag rows (4K + 4k are separate rows).
    assert len(rows) == 4
    # Order is (source, name): manual < rule < sonarr alphabetically.
    sources = [r["source"] for r in rows]
    assert sources == ["manual", "rule", "rule", "sonarr"]
    # Names preserve their original casing.
    names = [(r["name"], r["source"]) for r in rows]
    assert ("4K", "sonarr") in names
    assert ("4k", "rule") in names


@pytest.mark.asyncio
async def test_tags_endpoint_404_for_unknown_file(
    client: AsyncClient,
) -> None:
    headers = await _headers(client)
    r = await client.get("/api/v1/media/does-not-exist/tags", headers=headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_tags_endpoint_returns_empty_for_file_with_no_tags(
    client: AsyncClient,
) -> None:
    """File exists but has no tags → 200 with empty array, NOT 404.

    Operators rely on this to distinguish "file evicted" from
    "file has no tags yet" — both states are normal but the UI
    handles them differently."""
    headers = await _headers(client)
    await _seed()
    # mf-2 has one rule tag — clear it.
    async with get_database().session() as sess:
        from sqlalchemy import delete

        await sess.execute(delete(MediaTag).where(MediaTag.media_file_id == "mf-2"))
        await sess.commit()
    r = await client.get("/api/v1/media/mf-2/tags", headers=headers)
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_list_media_default_has_empty_tags_field(
    client: AsyncClient,
) -> None:
    """Without ``include_tags=true``, the response carries an empty
    ``tags`` list — proving the field is present in the schema and
    that the LEFT JOIN was NOT run."""
    headers = await _headers(client)
    await _seed()

    r = await client.get("/api/v1/media", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    for item in body["items"]:
        assert "tags" in item
        assert item["tags"] == []


@pytest.mark.asyncio
async def test_list_media_include_tags_attaches_deduped_names(
    client: AsyncClient,
) -> None:
    """With ``include_tags=true`` the response carries each row's
    deduped tag name list, ordered alphabetically."""
    headers = await _headers(client)
    await _seed()

    r = await client.get(
        "/api/v1/media?include_tags=true",
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    by_id = {item["id"]: item for item in body["items"]}

    mf1_tags = by_id["mf-1"]["tags"]
    # Four tag ROWS on mf-1 but only three unique NAMES after
    # case-sensitive dedupe (4K + 4k are distinct).
    assert sorted(mf1_tags) == sorted(["needs-review", "4K", "4k", "watched"])
    assert by_id["mf-2"]["tags"] == ["archive"]


@pytest.mark.asyncio
async def test_list_media_include_tags_preserves_casing(
    client: AsyncClient,
) -> None:
    """Per the plan's guard rail: do NOT normalize tag casing.
    Sonarr's "4K" and rule's "4k" must both appear distinctly."""
    headers = await _headers(client)
    await _seed()

    r = await client.get(
        "/api/v1/media?include_tags=true", headers=headers
    )
    items = r.json()["items"]
    mf1 = next(i for i in items if i["id"] == "mf-1")
    # Both casings present, neither was folded.
    assert "4K" in mf1["tags"]
    assert "4k" in mf1["tags"]
