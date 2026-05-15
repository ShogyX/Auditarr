"""Stage 31 — Codec / container filter on Files.

Pins the new query-string filters on ``GET /api/v1/media``:

  - ``video_codec`` and ``container`` query params accept either
    a single value (equality) or a comma-separated list (IN clause).
  - Empty string or all-empty CSV is silently dropped.
  - Filters compose with existing filters (severity, library_id,
    search, etc.).
  - The values are stable across pagination.

We don't re-validate the scanner / ffprobe behavior here — those
have their own test suites. Stage 31 only widens the read
surface; no scanner changes.
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
    db_path = tmp_path / "stage31.db"
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
    """Seed a small representative dataset:

      - 3 hevc/matroska
      - 2 h264/mp4
      - 1 mpeg4/avi
      - 1 unprobed (video_codec is None, container is None)

    Returns the library id.
    """
    async with get_database().session() as sess:
        lib = Library(name="L", root_path="/lib", kind="movies")
        sess.add(lib)
        await sess.flush()
        rows = [
            ("hevc", "matroska", "m1.mkv"),
            ("hevc", "matroska", "m2.mkv"),
            ("hevc", "matroska", "m3.mkv"),
            ("h264", "mp4", "m4.mp4"),
            ("h264", "mp4", "m5.mp4"),
            ("mpeg4", "avi", "m6.avi"),
            (None, None, "m7.unprobed"),
        ]
        for codec, container, name in rows:
            sess.add(
                MediaFile(
                    library_id=lib.id,
                    path=f"/lib/{name}",
                    relative_path=name,
                    filename=name,
                    extension=name.rsplit(".", 1)[-1],
                    size_bytes=100,
                    mtime=utcnow(),
                    category="media",
                    severity="ok",
                    severity_rank=10,
                    video_codec=codec,
                    container=container,
                    has_subtitles=False,
                    seen_at=utcnow(),
                    is_orphaned=False,
                )
            )
        await sess.commit()
        return lib.id


# ── Tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_video_codec_filter(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    await _seed_files()

    response = await client.get(
        "/api/v1/media?video_codec=hevc", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert all(item["video_codec"] == "hevc" for item in body["items"])


@pytest.mark.asyncio
async def test_multi_video_codec_filter_is_in_clause(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    await _seed_files()

    response = await client.get(
        "/api/v1/media?video_codec=hevc,h264", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5  # 3 hevc + 2 h264
    assert all(
        item["video_codec"] in {"hevc", "h264"} for item in body["items"]
    )


@pytest.mark.asyncio
async def test_single_container_filter(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    await _seed_files()

    response = await client.get(
        "/api/v1/media?container=matroska", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert all(item["container"] == "matroska" for item in body["items"])


@pytest.mark.asyncio
async def test_multi_container_filter(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    await _seed_files()

    response = await client.get(
        "/api/v1/media?container=mp4,avi", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3  # 2 mp4 + 1 avi


@pytest.mark.asyncio
async def test_codec_and_container_compose(client: AsyncClient) -> None:
    """Both filters AND together (not OR). h264 + mp4 → 2 files;
    h264 + matroska → 0 files (no h264 happens to be matroska)."""
    headers = await _admin_headers(client)
    await _seed_files()

    response = await client.get(
        "/api/v1/media?video_codec=h264&container=mp4", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["total"] == 2

    response = await client.get(
        "/api/v1/media?video_codec=h264&container=matroska", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_unprobed_excluded_by_codec_filter(client: AsyncClient) -> None:
    """The unprobed row (video_codec=None) must NOT appear when
    a codec filter is active. SQL ``= 'hevc'`` against NULL is
    UNKNOWN, which excludes it — confirming we don't accidentally
    coerce NULL to a comparable value."""
    headers = await _admin_headers(client)
    await _seed_files()

    # Without any codec filter: all 7 visible.
    response = await client.get("/api/v1/media", headers=headers)
    assert response.json()["total"] == 7

    # With codec filter: 3 hevc, the unprobed row is excluded.
    response = await client.get(
        "/api/v1/media?video_codec=hevc", headers=headers
    )
    assert response.json()["total"] == 3


@pytest.mark.asyncio
async def test_empty_codec_value_is_a_noop(client: AsyncClient) -> None:
    """A trailing comma or all-empty CSV must be silently dropped.
    The UI may send these during deselection; the API shouldn't
    treat them as a "match the empty string" filter (which would
    match zero rows and surprise the operator)."""
    headers = await _admin_headers(client)
    await _seed_files()

    # Trailing comma after a value.
    response = await client.get(
        "/api/v1/media?video_codec=hevc,", headers=headers
    )
    assert response.json()["total"] == 3

    # All-empty CSV → treat as no filter.
    response = await client.get(
        "/api/v1/media?video_codec=,,,", headers=headers
    )
    assert response.json()["total"] == 7


@pytest.mark.asyncio
async def test_codec_filter_composes_with_severity(
    client: AsyncClient,
) -> None:
    """The new filters must compose with existing ones, not replace
    them. We bump one hevc row to severity=warn and check that
    ``video_codec=hevc&severity=warn`` returns exactly 1."""
    headers = await _admin_headers(client)
    await _seed_files()
    async with get_database().session() as sess:
        await sess.execute(
            update(MediaFile)
            .where(MediaFile.filename == "m1.mkv")
            .values(severity="warn", severity_rank=50)
        )
        await sess.commit()

    response = await client.get(
        "/api/v1/media?video_codec=hevc&severity=warn", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_pagination_stable_under_filter(client: AsyncClient) -> None:
    """A filtered list paginated as two pages of 2 + 1 must
    return all 3 hevc rows without duplicates and without
    skipping. The default ordering (severity-first then path)
    is already deterministic; this test pins that against the
    codec filter."""
    headers = await _admin_headers(client)
    await _seed_files()

    page1 = await client.get(
        "/api/v1/media?video_codec=hevc&offset=0&limit=2", headers=headers
    )
    page2 = await client.get(
        "/api/v1/media?video_codec=hevc&offset=2&limit=2", headers=headers
    )
    seen = {it["id"] for it in page1.json()["items"]} | {
        it["id"] for it in page2.json()["items"]
    }
    assert len(seen) == 3  # all 3 hevc rows, none skipped or doubled
