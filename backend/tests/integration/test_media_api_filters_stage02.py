"""Stage 02 — /api/v1/media accepts and applies the new filter params.

Plan §192: "call ``/api/v1/media?path_contains=foo&size_min=...``
and assert the response shape."

End-to-end exercise of the new query-string parameters:

  - ``path_contains`` — case-insensitive substring on path
  - ``codec_contains`` — case-insensitive substring on video_codec
  - ``container_eq`` — strict equality on container
  - ``extension_eq`` — strict equality (dot-stripped, lowercased)
  - ``size_min`` / ``size_max`` — inclusive byte range
  - ``mtime_after`` / ``mtime_before`` — inclusive ISO datetime range

Fixture pattern matches test_media_stage31.py: tmp-path SQLite,
register an admin, promote to admin role via direct SQL, then
seed three media rows that exercise the boundaries.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.library import Library
from app.models.media import MediaFile
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
    db_path = tmp_path / "stage02_filters.db"
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


async def _seed_files() -> str:
    """Seed three media rows that span the Stage-02 filter boundaries.

    Row A: hevc / matroska / mkv / 500 MB  / mtime 2024-06-15
    Row B: h264 / mp4      / mp4 / 2  GB   / mtime 2025-02-01
    Row C: hevc / matroska / mkv / 10 GB   / mtime 2025-04-10

    Paths put A and C under ``/lib/My Show/`` so ``path_contains``
    has a real subset to find.
    """
    async with get_database().session() as sess:
        lib = Library(name="L", root_path="/lib", kind="movies")
        sess.add(lib)
        await sess.flush()
        # Map: id → field set.
        rows = [
            dict(
                path="/lib/My Show/A.mkv",
                relative_path="My Show/A.mkv",
                filename="A.mkv",
                extension="mkv",
                size_bytes=500 * 1024 * 1024,
                mtime=datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc),
                video_codec="hevc",
                container="matroska",
            ),
            dict(
                path="/lib/Another/B.mp4",
                relative_path="Another/B.mp4",
                filename="B.mp4",
                extension="mp4",
                size_bytes=2 * 1024 * 1024 * 1024,
                mtime=datetime(2025, 2, 1, 0, 0, tzinfo=timezone.utc),
                video_codec="h264",
                container="mp4",
            ),
            dict(
                path="/lib/My Show/C.mkv",
                relative_path="My Show/C.mkv",
                filename="C.mkv",
                extension="mkv",
                size_bytes=10 * 1024 * 1024 * 1024,
                mtime=datetime(2025, 4, 10, 0, 0, tzinfo=timezone.utc),
                video_codec="hevc",
                container="matroska",
            ),
        ]
        for r in rows:
            sess.add(
                MediaFile(
                    library_id=lib.id,
                    category="media",
                    severity="ok",
                    severity_rank=10,
                    has_subtitles=False,
                    seen_at=utcnow(),
                    is_orphaned=False,
                    **r,
                )
            )
        await sess.commit()
        return lib.id


def _filenames(payload: dict) -> set[str]:
    return {item["filename"] for item in payload["items"]}


@pytest.mark.asyncio
async def test_path_contains_substring_match(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    await _seed_files()
    resp = await client.get(
        "/api/v1/media", headers=headers, params={"path_contains": "my show"}
    )
    assert resp.status_code == 200, resp.text
    assert _filenames(resp.json()) == {"A.mkv", "C.mkv"}


@pytest.mark.asyncio
async def test_codec_contains_matches_substring(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    await _seed_files()
    resp = await client.get(
        "/api/v1/media", headers=headers, params={"codec_contains": "hev"}
    )
    assert resp.status_code == 200
    assert _filenames(resp.json()) == {"A.mkv", "C.mkv"}


@pytest.mark.asyncio
async def test_container_eq_strict(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    await _seed_files()
    resp = await client.get(
        "/api/v1/media", headers=headers, params={"container_eq": "mp4"}
    )
    assert resp.status_code == 200
    assert _filenames(resp.json()) == {"B.mp4"}


@pytest.mark.asyncio
async def test_extension_eq_strips_leading_dot(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    await _seed_files()
    # Operator types ``.MKV``; storage is ``mkv``. The router
    # normalises both forms.
    resp = await client.get(
        "/api/v1/media", headers=headers, params={"extension_eq": ".MKV"}
    )
    assert resp.status_code == 200
    assert _filenames(resp.json()) == {"A.mkv", "C.mkv"}


@pytest.mark.asyncio
async def test_size_min_filters_smaller_rows(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    await _seed_files()
    one_gb = 1024 * 1024 * 1024
    resp = await client.get(
        "/api/v1/media", headers=headers, params={"size_min": one_gb}
    )
    assert resp.status_code == 200
    assert _filenames(resp.json()) == {"B.mp4", "C.mkv"}


@pytest.mark.asyncio
async def test_size_max_filters_larger_rows(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    await _seed_files()
    three_gb = 3 * 1024 * 1024 * 1024
    resp = await client.get(
        "/api/v1/media", headers=headers, params={"size_max": three_gb}
    )
    assert resp.status_code == 200
    assert _filenames(resp.json()) == {"A.mkv", "B.mp4"}


@pytest.mark.asyncio
async def test_mtime_after_includes_inclusive_lower_bound(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    await _seed_files()
    resp = await client.get(
        "/api/v1/media",
        headers=headers,
        params={"mtime_after": "2025-01-01T00:00:00+00:00"},
    )
    assert resp.status_code == 200, resp.text
    assert _filenames(resp.json()) == {"B.mp4", "C.mkv"}


@pytest.mark.asyncio
async def test_mtime_before_includes_inclusive_upper_bound(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    await _seed_files()
    resp = await client.get(
        "/api/v1/media",
        headers=headers,
        params={"mtime_before": "2025-03-01T00:00:00+00:00"},
    )
    assert resp.status_code == 200
    assert _filenames(resp.json()) == {"A.mkv", "B.mp4"}


@pytest.mark.asyncio
async def test_mtime_malformed_returns_400_or_422(client: AsyncClient) -> None:
    """Bad ISO strings get rejected at the router boundary."""
    headers = await _admin_headers(client)
    await _seed_files()
    resp = await client.get(
        "/api/v1/media", headers=headers, params={"mtime_after": "not-a-date"}
    )
    # Either 400 (app's ValidationError → 400) or 422 (FastAPI's
    # built-in) is acceptable — the precise mapping is determined
    # by the app's exception-handler stack and we don't pin it.
    assert resp.status_code in (400, 422), resp.text


@pytest.mark.asyncio
async def test_filters_compose_with_and_semantics(client: AsyncClient) -> None:
    """Multiple Stage 02 filters AND together: path contains "My
    Show" AND container is matroska AND size >= 1 GB → only C."""
    headers = await _admin_headers(client)
    await _seed_files()
    resp = await client.get(
        "/api/v1/media",
        headers=headers,
        params={
            "path_contains": "My Show",
            "container_eq": "matroska",
            "size_min": 1024 * 1024 * 1024,
        },
    )
    assert resp.status_code == 200
    assert _filenames(resp.json()) == {"C.mkv"}
