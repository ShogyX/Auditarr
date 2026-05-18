"""Distinct-values endpoint (v1.9 Stage 3.1).

Pins the contract:

  1. ``GET /api/v1/media/distinct?field=<col>`` returns up to 200
     {value, count} rows sorted by count desc, for any column in
     the whitelist.
  2. NULL values surface as ``value: null`` (the popover renders
     them as "(none)").
  3. ``library_id=`` scopes the aggregation.
  4. ``prefix=`` does a case-insensitive prefix match.
  5. JSON-list columns (subtitle_languages, audio_languages) are
     aggregated correctly: a file tagged ``[en, es]`` contributes
     1 to each language's count.
  6. Non-media files (category != "media") are excluded — the
     same scope rule as the composition service.
  7. Unknown ``field`` returns 422.
  8. The route ordering issue (``/distinct`` vs ``/{media_id}``)
     is handled — calling the endpoint by its actual path
     resolves to ``media_distinct``, not ``get_media``.
"""

from __future__ import annotations

import datetime as _dt
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
    db_path = tmp_path / "distinct.db"
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
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "user@example.com",
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


def _mf(
    *,
    library_id: str,
    filename: str,
    category: str = "media",
    video_codec: str | None = None,
    audio_languages: list[str] | None = None,
    subtitle_languages: list[str] | None = None,
    extension: str | None = None,
) -> MediaFile:
    return MediaFile(
        library_id=library_id,
        path=f"/lib/{filename}",
        relative_path=filename,
        filename=filename,
        extension=(extension or (filename.rsplit(".", 1)[1] if "." in filename else "")),
        size_bytes=1024,
        mtime=_dt.datetime.now(_dt.UTC),
        category=category,
        severity="ok",
        severity_rank=10,
        video_codec=video_codec,
        audio_languages=audio_languages,
        subtitle_languages=subtitle_languages,
        has_subtitles=False,
        probe_failed=False,
        is_orphaned=False,
        seen_at=utcnow(),
    )


async def _seed(rows: list[MediaFile]) -> str:
    async with get_database().session() as sess:
        lib = Library(name="movies", root_path="/lib", kind="movies")
        sess.add(lib)
        await sess.flush()
        for row in rows:
            row.library_id = lib.id
            sess.add(row)
        await sess.commit()
        return lib.id


@pytest.mark.asyncio
async def test_distinct_scalar_returns_counts(client: AsyncClient) -> None:
    headers = await _user_headers(client)
    await _seed(
        [
            _mf(library_id="x", filename="a.mkv", video_codec="h264"),
            _mf(library_id="x", filename="b.mkv", video_codec="h264"),
            _mf(library_id="x", filename="c.mkv", video_codec="hevc"),
        ]
    )
    r = await client.get(
        "/api/v1/media/distinct?field=video_codec",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["field"] == "video_codec"
    assert body["truncated"] is False
    # Sorted by count desc.
    by_value = {row["value"]: row["count"] for row in body["values"]}
    assert by_value == {"h264": 2, "hevc": 1}


@pytest.mark.asyncio
async def test_distinct_surfaces_null_bucket(client: AsyncClient) -> None:
    headers = await _user_headers(client)
    await _seed(
        [
            _mf(library_id="x", filename="a.mkv", video_codec="h264"),
            _mf(library_id="x", filename="b.mkv", video_codec=None),
            _mf(library_id="x", filename="c.mkv", video_codec=None),
        ]
    )
    r = await client.get(
        "/api/v1/media/distinct?field=video_codec",
        headers=headers,
    )
    body = r.json()
    null_row = next((row for row in body["values"] if row["value"] is None), None)
    assert null_row is not None
    assert null_row["count"] == 2


@pytest.mark.asyncio
async def test_distinct_library_id_scoping(client: AsyncClient) -> None:
    headers = await _user_headers(client)
    async with get_database().session() as sess:
        lib_a = Library(name="a", root_path="/a", kind="movies")
        lib_b = Library(name="b", root_path="/b", kind="movies")
        sess.add_all([lib_a, lib_b])
        await sess.flush()
        sess.add_all(
            [
                _mf(library_id=lib_a.id, filename="x.mkv", video_codec="h264"),
                _mf(library_id=lib_b.id, filename="y.mkv", video_codec="hevc"),
            ]
        )
        await sess.commit()
        lib_a_id = lib_a.id

    r = await client.get(
        f"/api/v1/media/distinct?field=video_codec&library_id={lib_a_id}",
        headers=headers,
    )
    body = r.json()
    by_value = {row["value"]: row["count"] for row in body["values"]}
    assert by_value == {"h264": 1}


@pytest.mark.asyncio
async def test_distinct_prefix_match_case_insensitive(
    client: AsyncClient,
) -> None:
    headers = await _user_headers(client)
    await _seed(
        [
            _mf(library_id="x", filename="a.mkv", video_codec="h264"),
            _mf(library_id="x", filename="b.mkv", video_codec="h265"),
            _mf(library_id="x", filename="c.mkv", video_codec="hevc"),
            _mf(library_id="x", filename="d.mkv", video_codec="av1"),
        ]
    )
    # Prefix "H" (upper-case) should still match h-prefixed values.
    r = await client.get(
        "/api/v1/media/distinct?field=video_codec&prefix=H",
        headers=headers,
    )
    body = r.json()
    values = {row["value"] for row in body["values"]}
    assert values == {"h264", "h265", "hevc"}
    assert "av1" not in values


@pytest.mark.asyncio
async def test_distinct_json_list_aggregates_per_element(
    client: AsyncClient,
) -> None:
    """A file with ``audio_languages=[en, es]`` contributes 1 to
    each language's count."""
    headers = await _user_headers(client)
    await _seed(
        [
            _mf(library_id="x", filename="a.mkv", audio_languages=["en", "es"]),
            _mf(library_id="x", filename="b.mkv", audio_languages=["en"]),
        ]
    )
    r = await client.get(
        "/api/v1/media/distinct?field=audio_languages",
        headers=headers,
    )
    body = r.json()
    by_value = {row["value"]: row["count"] for row in body["values"]}
    assert by_value == {"en": 2, "es": 1}


@pytest.mark.asyncio
async def test_distinct_excludes_non_media_category(
    client: AsyncClient,
) -> None:
    """A sidecar .srt file shouldn't pollute the extension
    distinct list — same scope rule as the composition service."""
    headers = await _user_headers(client)
    await _seed(
        [
            _mf(library_id="x", filename="a.mkv", extension="mkv"),
            _mf(library_id="x", filename="b.mkv", extension="mkv"),
            _mf(
                library_id="x",
                filename="a.srt",
                category="subtitle",
                extension="srt",
            ),
        ]
    )
    r = await client.get(
        "/api/v1/media/distinct?field=extension",
        headers=headers,
    )
    body = r.json()
    by_value = {row["value"]: row["count"] for row in body["values"]}
    assert by_value == {"mkv": 2}
    assert "srt" not in by_value


@pytest.mark.asyncio
async def test_distinct_unknown_field_returns_422(
    client: AsyncClient,
) -> None:
    headers = await _user_headers(client)
    r = await client.get(
        "/api/v1/media/distinct?field=hash_sha256",
        headers=headers,
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_distinct_route_resolves_correctly(
    client: AsyncClient,
) -> None:
    """``/media/distinct`` must hit the distinct handler, NOT the
    ``/media/{media_id}`` detail handler (which would 404 trying
    to find a file with id="distinct"). Pin this so a future
    route refactor doesn't silently break the popover."""
    headers = await _user_headers(client)
    # No seeded data — distinct returns an empty list, NOT 404.
    r = await client.get(
        "/api/v1/media/distinct?field=severity",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["field"] == "severity"
    assert body["values"] == []
