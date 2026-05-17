"""Stage 15 (plan §662) — ``GET /media/vocabulary`` returns
the distinct codec / container / extension / tag values
currently in the indexed library.

Coverage:
  * Empty library → all five lists empty.
  * Populated library → distinct values, sorted, no NULLs.
  * Tags from ``media_tags`` are surfaced too.
  * The 60s in-process cache is hit on a second call within
    the window.
  * The cache CAN be invalidated via the test hook (so we
    can assert fresh DB state vs cached state).
  * Anonymous access is rejected (auth required).
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
from app.models.tag import MediaTag
from app.models.user import User
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database
from app.utils.datetime import utcnow


PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def vocab_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "vocab.db"
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

    # Clear the in-process TTL cache at setup so prior test
    # files don't leak vocabularies into this one.
    from app.api.v1.media import _vocabulary_cache_clear

    _vocabulary_cache_clear()

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as c:
            yield c
    finally:
        _vocabulary_cache_clear()
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
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _seed_files_and_tags(
    *,
    video_codecs: list[str],
    audio_codecs: list[str],
    containers: list[str],
    extensions: list[str],
    tags: list[str],
) -> None:
    """Cross-join the input lists into one file each; add the
    tags as MediaTag rows pointing at the first file."""
    now = utcnow()
    async with get_database().session() as sess:
        lib = Library(name="Lib", root_path="/data/lib", kind="movies")
        sess.add(lib)
        await sess.flush()

        # Build one file per (codec, container) combination so
        # every distinct value is represented. The total file
        # count is small (5 × 5 = 25 max) — plenty for the
        # endpoint's SELECT DISTINCT to chew through.
        rows = []
        i = 0
        for vc in video_codecs or [None]:
            for ac in audio_codecs or [None]:
                for ct in containers or [None]:
                    for ext in extensions or ["mkv"]:
                        rows.append(
                            MediaFile(
                                library_id=lib.id,
                                path=f"/data/lib/f{i}.{ext}",
                                relative_path=f"f{i}.{ext}",
                                filename=f"f{i}.{ext}",
                                extension=ext,
                                size_bytes=100,
                                mtime=now,
                                category="media",
                                severity="ok",
                                severity_rank=10,
                                container=ct,
                                video_codec=vc,
                                audio_codec=ac,
                                seen_at=now,
                                is_orphaned=False,
                            )
                        )
                        i += 1
        sess.add_all(rows)
        await sess.flush()

        # Attach the requested tags to the first file (with
        # a different `source` for variety).
        if rows and tags:
            for tag in tags:
                sess.add(
                    MediaTag(
                        media_file_id=rows[0].id,
                        name=tag,
                        source="manual",
                    )
                )
        await sess.commit()


# ── Test 1 — empty library returns five empty lists


@pytest.mark.asyncio
async def test_vocabulary_empty_library(vocab_client: AsyncClient) -> None:
    headers = await _admin_headers(vocab_client)
    resp = await vocab_client.get("/api/v1/media/vocabulary", headers=headers)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload == {
        "video_codecs": [],
        "audio_codecs": [],
        "containers": [],
        "extensions": [],
        "tags": [],
    }


# ── Test 2 — populated library returns distinct sorted values


@pytest.mark.asyncio
async def test_vocabulary_returns_distinct_sorted_values(
    vocab_client: AsyncClient,
) -> None:
    await _seed_files_and_tags(
        video_codecs=["hevc", "h264", "av1"],
        audio_codecs=["eac3", "aac"],
        containers=["mkv", "mp4"],
        extensions=["mkv", "mp4"],
        tags=["plex:1080p", "sonarr:downloaded", "manual:keep"],
    )
    headers = await _admin_headers(vocab_client)
    resp = await vocab_client.get("/api/v1/media/vocabulary", headers=headers)
    assert resp.status_code == 200
    payload = resp.json()

    # Sorted ascending, duplicates removed.
    assert payload["video_codecs"] == ["av1", "h264", "hevc"]
    assert payload["audio_codecs"] == ["aac", "eac3"]
    assert payload["containers"] == ["mkv", "mp4"]
    assert payload["extensions"] == ["mkv", "mp4"]
    assert payload["tags"] == ["manual:keep", "plex:1080p", "sonarr:downloaded"]


# ── Test 3 — NULL columns are excluded


@pytest.mark.asyncio
async def test_vocabulary_excludes_null_codecs(
    vocab_client: AsyncClient,
) -> None:
    """Non-media files have NULL for codecs/container. They
    must not surface in the vocabulary (the dropdown would
    show 'null' otherwise, which is nonsense)."""
    now = utcnow()
    async with get_database().session() as sess:
        lib = Library(name="X", root_path="/x", kind="movies")
        sess.add(lib)
        await sess.flush()
        sess.add_all(
            [
                # A media file with a codec...
                MediaFile(
                    library_id=lib.id,
                    path="/x/a.mkv",
                    relative_path="a.mkv",
                    filename="a.mkv",
                    extension="mkv",
                    size_bytes=100,
                    mtime=now,
                    category="media",
                    severity="ok",
                    severity_rank=10,
                    container="mkv",
                    video_codec="hevc",
                    audio_codec="aac",
                    seen_at=now,
                    is_orphaned=False,
                ),
                # ...and a non-media file with NULLs everywhere.
                MediaFile(
                    library_id=lib.id,
                    path="/x/b.nfo",
                    relative_path="b.nfo",
                    filename="b.nfo",
                    extension="nfo",
                    size_bytes=10,
                    mtime=now,
                    category="metadata",
                    severity="ok",
                    severity_rank=10,
                    container=None,
                    video_codec=None,
                    audio_codec=None,
                    seen_at=now,
                    is_orphaned=False,
                ),
            ]
        )
        await sess.commit()

    headers = await _admin_headers(vocab_client)
    resp = await vocab_client.get("/api/v1/media/vocabulary", headers=headers)
    payload = resp.json()
    # No NULL or empty-string sneaks through.
    assert payload["video_codecs"] == ["hevc"]
    assert payload["audio_codecs"] == ["aac"]
    assert payload["containers"] == ["mkv"]
    assert "nfo" in payload["extensions"]
    assert "mkv" in payload["extensions"]
    assert "" not in payload["video_codecs"]
    assert None not in payload["video_codecs"]


# ── Test 4 — second call within TTL window hits the cache


@pytest.mark.asyncio
async def test_vocabulary_cache_returns_stale_after_db_change(
    vocab_client: AsyncClient,
) -> None:
    """Plan §656 — cache for 60s. Within that window a second
    call should return the cached payload even if the DB has
    changed underneath us. The test hook
    ``_vocabulary_cache_clear`` then lets us prove the new
    state IS visible after a flush."""
    headers = await _admin_headers(vocab_client)

    # First call: empty library.
    r1 = await vocab_client.get("/api/v1/media/vocabulary", headers=headers)
    assert r1.json()["video_codecs"] == []

    # Mutate the DB.
    await _seed_files_and_tags(
        video_codecs=["av1"],
        audio_codecs=[],
        containers=[],
        extensions=["mkv"],
        tags=[],
    )

    # Second call — within the 60s window, should still be
    # cached (empty).
    r2 = await vocab_client.get("/api/v1/media/vocabulary", headers=headers)
    assert r2.json()["video_codecs"] == [], (
        f"expected cached empty result; got {r2.json()}"
    )

    # Flush the cache and re-query: now we see the new row.
    from app.api.v1.media import _vocabulary_cache_clear

    _vocabulary_cache_clear()
    r3 = await vocab_client.get("/api/v1/media/vocabulary", headers=headers)
    assert r3.json()["video_codecs"] == ["av1"]


# ── Test 5 — anonymous access rejected


@pytest.mark.asyncio
async def test_vocabulary_requires_auth(vocab_client: AsyncClient) -> None:
    resp = await vocab_client.get("/api/v1/media/vocabulary")
    # CurrentUser dependency rejects unauthenticated calls.
    assert resp.status_code in (401, 403), resp.text


# ── Test 6 — /media/vocabulary is NOT shadowed by /media/{media_id}


@pytest.mark.asyncio
async def test_vocabulary_route_not_shadowed(
    vocab_client: AsyncClient,
) -> None:
    """Defensive — the new endpoint sits at /media/vocabulary
    while /media/{media_id} also exists. FastAPI matches in
    declaration order; this test pins that the literal route
    is reachable rather than being interpreted as
    media_id="vocabulary"."""
    headers = await _admin_headers(vocab_client)
    resp = await vocab_client.get("/api/v1/media/vocabulary", headers=headers)
    # If the route were shadowed, this would 404 (no media
    # file with id "vocabulary") rather than 200 with the
    # vocabulary payload.
    assert resp.status_code == 200
    body = resp.json()
    # Vocabulary shape, not MediaFileDetail shape.
    assert "video_codecs" in body
    assert "id" not in body
