"""v1.9 Stage 9.5.7 (OP-8 / OP-9) — dashboard language-preference
surfaces.

Pins two new endpoints:
  * GET /api/v1/dashboard/foreign-audio
  * GET /api/v1/dashboard/incompatible-media

The first is configured via Settings.preferred_audio_languages
and Settings.preferred_subtitle_languages. The second counts
files carrying any tag whose name contains 'incompatible'.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.main import create_app
from app.models.library import Library
from app.models.media import MediaFile
from app.models.tag import MediaTag
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
    db_path = tmp_path / "foreign_audio.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    from app.core.settings import get_settings

    get_settings.cache_clear()
    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001
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


async def _seed_library() -> str:
    """Create one library, return its id."""
    async with get_database().session() as sess:
        lib = Library(name="Movies", root_path="/data/movies", kind="movies")
        sess.add(lib)
        await sess.commit()
        return lib.id


async def _seed_media(
    library_id: str,
    *,
    audio_languages: list[str] | None,
    subtitle_languages: list[str] | None,
    path: str,
    tags: list[str] | None = None,
) -> str:
    now = utcnow()
    async with get_database().session() as sess:
        mf = MediaFile(
            library_id=library_id,
            path=path,
            relative_path=path.rsplit("/", 1)[-1],
            filename=path.rsplit("/", 1)[-1],
            extension="mkv",
            size_bytes=100 * 1024 * 1024,
            mtime=now,
            category="media",
            severity="ok",
            severity_rank=10,
            has_subtitles=bool(subtitle_languages),
            seen_at=now,
            is_orphaned=False,
            audio_languages=audio_languages,
            subtitle_languages=subtitle_languages,
        )
        sess.add(mf)
        await sess.flush()
        for tag in tags or []:
            sess.add(
                MediaTag(
                    media_file_id=mf.id,
                    name=tag,
                    source="rule",
                )
            )
        await sess.commit()
        return mf.id


# ── /dashboard/foreign-audio ───────────────────────────────────


@pytest.mark.asyncio
async def test_foreign_audio_empty_library_returns_zero(
    client: AsyncClient,
) -> None:
    """An empty library returns count=0 and the configured
    preferences (defaults: eng)."""
    headers = await _admin_headers(client)
    await _seed_library()

    response = await client.get(
        "/api/v1/dashboard/foreign-audio", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 0
    assert body["sample_ids"] == []
    assert body["preferred_audio_languages"] == ["eng"]
    assert body["preferred_subtitle_languages"] == ["eng"]


@pytest.mark.asyncio
async def test_foreign_audio_counts_non_english_with_no_english_subs(
    client: AsyncClient,
) -> None:
    """A file with French primary audio + no English subs is a
    match. A file with French primary audio + English subs is
    not (saved by the subtitle). A file with English primary
    audio is not (not foreign in the first place)."""
    headers = await _admin_headers(client)
    library_id = await _seed_library()

    # Match: French audio, no preferred subs.
    matched_id = await _seed_media(
        library_id,
        audio_languages=["fra"],
        subtitle_languages=["fra"],
        path="/data/movies/french-no-eng-subs.mkv",
    )
    # Not a match: French audio BUT English subs save it.
    await _seed_media(
        library_id,
        audio_languages=["fra"],
        subtitle_languages=["fra", "eng"],
        path="/data/movies/french-with-eng-subs.mkv",
    )
    # Not a match: English primary audio.
    await _seed_media(
        library_id,
        audio_languages=["eng"],
        subtitle_languages=None,
        path="/data/movies/english-primary.mkv",
    )
    # Not a match: unknown primary audio (no signal — skipped).
    await _seed_media(
        library_id,
        audio_languages=["und"],
        subtitle_languages=None,
        path="/data/movies/unknown-audio.mkv",
    )

    response = await client.get(
        "/api/v1/dashboard/foreign-audio", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["sample_ids"] == [matched_id]


# ── /dashboard/incompatible-media ──────────────────────────────


@pytest.mark.asyncio
async def test_incompatible_media_empty_library_returns_zero(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    await _seed_library()
    response = await client.get(
        "/api/v1/dashboard/incompatible-media", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 0
    assert body["sample_ids"] == []


@pytest.mark.asyncio
async def test_incompatible_media_counts_any_incompatible_tag(
    client: AsyncClient,
) -> None:
    """Any tag whose name contains 'incompatible' qualifies a
    file. Multiple incompatible tags on the same file count it
    once. Files with only non-incompatible tags don't count."""
    headers = await _admin_headers(client)
    library_id = await _seed_library()

    # Match: plex-incompatible-video tag.
    a = await _seed_media(
        library_id,
        audio_languages=["eng"],
        subtitle_languages=None,
        path="/data/movies/a.mkv",
        tags=["plex-incompatible-video"],
    )
    # Match: jellyfin-incompatible-audio tag (different prefix).
    b = await _seed_media(
        library_id,
        audio_languages=["eng"],
        subtitle_languages=None,
        path="/data/movies/b.mkv",
        tags=["jellyfin-incompatible-audio"],
    )
    # Match (deduped): two incompatible tags on same file count once.
    c = await _seed_media(
        library_id,
        audio_languages=["eng"],
        subtitle_languages=None,
        path="/data/movies/c.mkv",
        tags=[
            "plex-incompatible-video",
            "plex-incompatible-audio",
        ],
    )
    # Not a match: a tag with no "incompatible" substring.
    await _seed_media(
        library_id,
        audio_languages=["eng"],
        subtitle_languages=None,
        path="/data/movies/d.mkv",
        tags=["fat-hevc", "watched"],
    )

    response = await client.get(
        "/api/v1/dashboard/incompatible-media", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 3
    assert set(body["sample_ids"]) == {a, b, c}
