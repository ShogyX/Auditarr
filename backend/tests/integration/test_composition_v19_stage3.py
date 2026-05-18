"""Composition endpoint (v1.9 Stage 3.3).

Pins the contract operators rely on:

  1. ``GET /api/v1/dashboard/composition`` returns one payload with
     every section the new Categories card needs.
  2. Resolution buckets group correctly: 480p / 720p / 1080p / 4K
     based on the ``height`` column. Files without height land in
     "unknown" (and the row only appears when non-zero).
  3. Containers are normalized via ``container_label`` —
     ``matroska`` → MKV, ``mov`` → MP4, so two files in the raw
     ``mov`` and ``mp4`` containers both count as MP4.
  4. Languages aggregate across the JSON list columns
     (subtitle_languages / audio_languages) — a file tagged
     ``[en, es]`` contributes 1 to each.
  5. v1.9 Stage 3.5: ``category != 'media'`` rows (sidecar .srt /
     .nfo / .jpg) are excluded from every section EXCEPT the
     external-subtitle count of subtitles_internal_external.
  6. The bitrate matrix groups by (library, resolution, codec,
     container) and only keeps cells with >= 3 files (a 1-sample
     "median" isn't actionable).
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
    db_path = tmp_path / "composition.db"
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
            "username": "user",
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
        json={"login": "user", "password": PASSWORD},
    )
    return {"authorization": f"Bearer {login.json()['access_token']}"}


def _mf(
    *,
    library_id: str,
    filename: str,
    category: str = "media",
    height: int | None = None,
    container: str | None = None,
    video_codec: str | None = None,
    audio_codec: str | None = "aac",
    subtitle_codec: str | None = None,
    subtitle_languages: list[str] | None = None,
    audio_languages: list[str] | None = None,
    has_subtitles: bool = False,
    is_orphaned: bool = False,
    probe_failed: bool = False,
    bitrate_kbps: int | None = None,
    size_bytes: int = 1024,
) -> MediaFile:
    """Construct an in-memory MediaFile row for seeding."""
    return MediaFile(
        library_id=library_id,
        path=f"/lib/{filename}",
        relative_path=filename,
        filename=filename,
        extension=filename.rsplit(".", 1)[1] if "." in filename else "",
        size_bytes=size_bytes,
        mtime=_dt.datetime.now(_dt.UTC),
        category=category,
        severity="ok",
        severity_rank=10,
        container=container,
        video_codec=video_codec,
        audio_codec=audio_codec,
        subtitle_codec=subtitle_codec,
        height=height,
        subtitle_languages=subtitle_languages,
        audio_languages=audio_languages,
        has_subtitles=has_subtitles,
        is_orphaned=is_orphaned,
        probe_failed=probe_failed,
        bitrate_kbps=bitrate_kbps,
        seen_at=utcnow(),
    )


async def _seed(rows: list[MediaFile]) -> str:
    """Seed a library + the given media rows. Returns library_id."""
    async with get_database().session() as sess:
        lib = Library(
            name="movies",
            root_path="/lib",
            kind="movies",
        )
        sess.add(lib)
        await sess.flush()
        for row in rows:
            row.library_id = lib.id
            sess.add(row)
        await sess.commit()
        return lib.id


@pytest.mark.asyncio
async def test_composition_endpoint_shape(client: AsyncClient) -> None:
    """Endpoint returns the full structured payload."""
    headers = await _user_headers(client)
    await _seed([_mf(library_id="x", filename="a.mkv", height=1080)])

    r = await client.get("/api/v1/dashboard/composition", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    expected_keys = {
        "resolutions",
        "extensions",
        "containers",
        "subtitle_formats",
        "subtitle_languages",
        "audio_languages",
        "unknown_tracks",
        "subtitles_internal_external",
        "orphan_count",
        "bitrate_matrix",
    }
    assert expected_keys.issubset(set(body.keys()))


@pytest.mark.asyncio
async def test_resolution_buckets_correct(client: AsyncClient) -> None:
    headers = await _user_headers(client)
    await _seed(
        [
            _mf(library_id="x", filename="a.mkv", height=480),
            _mf(library_id="x", filename="b.mkv", height=720),
            _mf(library_id="x", filename="c.mkv", height=720),
            _mf(library_id="x", filename="d.mkv", height=1080),
            _mf(library_id="x", filename="e.mkv", height=2160),
        ]
    )
    r = await client.get("/api/v1/dashboard/composition", headers=headers)
    body = r.json()
    by_key = {row["key"]: row["count"] for row in body["resolutions"]}
    assert by_key.get("480p") == 1
    assert by_key.get("720p") == 2
    assert by_key.get("1080p") == 1
    assert by_key.get("4k") == 1


@pytest.mark.asyncio
async def test_containers_normalized_and_merged(client: AsyncClient) -> None:
    """Two files in ``matroska`` and one in ``mkv`` should merge as
    a single MKV row with count=3."""
    headers = await _user_headers(client)
    await _seed(
        [
            _mf(library_id="x", filename="a.mkv", container="matroska"),
            _mf(library_id="x", filename="b.mkv", container="matroska"),
            _mf(library_id="x", filename="c.mkv", container="mkv"),
            _mf(library_id="x", filename="d.mp4", container="mov"),
            _mf(library_id="x", filename="e.mp4", container="mp4"),
        ]
    )
    r = await client.get("/api/v1/dashboard/composition", headers=headers)
    body = r.json()
    by_label = {row["label"]: row["count"] for row in body["containers"]}
    assert by_label.get("MKV") == 3
    assert by_label.get("MP4") == 2


@pytest.mark.asyncio
async def test_language_counts_aggregate_across_files(
    client: AsyncClient,
) -> None:
    headers = await _user_headers(client)
    await _seed(
        [
            _mf(
                library_id="x",
                filename="a.mkv",
                audio_languages=["en", "es"],
                subtitle_languages=["en"],
            ),
            _mf(
                library_id="x",
                filename="b.mkv",
                audio_languages=["en"],
                subtitle_languages=["en", "fr"],
            ),
        ]
    )
    r = await client.get("/api/v1/dashboard/composition", headers=headers)
    body = r.json()
    audio = {row["key"]: row["count"] for row in body["audio_languages"]}
    subs = {row["key"]: row["count"] for row in body["subtitle_languages"]}
    assert audio == {"en": 2, "es": 1}
    assert subs == {"en": 2, "fr": 1}


@pytest.mark.asyncio
async def test_sidecar_files_excluded_from_media_sections(
    client: AsyncClient,
) -> None:
    """A .srt subtitle row (category=subtitle) and a .nfo
    metadata row (category=metadata) must NOT appear in the
    resolutions / extensions / containers totals."""
    headers = await _user_headers(client)
    await _seed(
        [
            _mf(library_id="x", filename="a.mkv", height=1080, container="matroska"),
            _mf(library_id="x", filename="a.srt", category="subtitle"),
            _mf(library_id="x", filename="a.nfo", category="metadata"),
        ]
    )
    r = await client.get("/api/v1/dashboard/composition", headers=headers)
    body = r.json()
    # Extensions section should not include .srt or .nfo.
    ext_keys = {row["key"] for row in body["extensions"]}
    assert "srt" not in ext_keys
    assert "nfo" not in ext_keys
    assert "mkv" in ext_keys
    # The internal/external split DOES count the .srt:
    assert body["subtitles_internal_external"]["external"] == 1


@pytest.mark.asyncio
async def test_unknown_tracks_count_probed_files_with_null_codecs(
    client: AsyncClient,
) -> None:
    """A successfully probed file (probe_failed=False) with a NULL
    video_codec or audio_codec contributes to the unknown-track
    counts. Files where probe_failed=True don't count — they're a
    separate failure mode."""
    headers = await _user_headers(client)
    await _seed(
        [
            _mf(
                library_id="x",
                filename="a.mkv",
                video_codec="h264",
                audio_codec="aac",
            ),
            _mf(
                library_id="x",
                filename="b.mkv",
                video_codec=None,
                audio_codec="aac",
                probe_failed=False,
            ),
            _mf(
                library_id="x",
                filename="c.mkv",
                video_codec="h264",
                audio_codec=None,
                probe_failed=False,
            ),
            _mf(
                library_id="x",
                filename="d.mkv",
                video_codec=None,
                audio_codec=None,
                probe_failed=True,  # Probe failed — doesn't count.
            ),
        ]
    )
    r = await client.get("/api/v1/dashboard/composition", headers=headers)
    body = r.json()
    assert body["unknown_tracks"]["video_unknown_count"] == 1
    assert body["unknown_tracks"]["audio_unknown_count"] == 1


@pytest.mark.asyncio
async def test_orphan_count_only_for_media_category(
    client: AsyncClient,
) -> None:
    headers = await _user_headers(client)
    await _seed(
        [
            _mf(library_id="x", filename="a.mkv", is_orphaned=True),
            _mf(library_id="x", filename="b.mkv", is_orphaned=False),
            # A sidecar .srt marked orphan should NOT count toward
            # the media-orphan count — the operator's mental model
            # is "missing video files", not "missing subtitles".
            _mf(
                library_id="x",
                filename="a.srt",
                category="subtitle",
                is_orphaned=True,
            ),
        ]
    )
    r = await client.get("/api/v1/dashboard/composition", headers=headers)
    body = r.json()
    assert body["orphan_count"] == 1


@pytest.mark.asyncio
async def test_bitrate_matrix_drops_cells_with_fewer_than_three_files(
    client: AsyncClient,
) -> None:
    """A cell with <3 files isn't a meaningful median — the matrix
    should suppress those rows."""
    headers = await _user_headers(client)
    await _seed(
        [
            # 3 files in the same (1080p, h264, matroska) cell → kept.
            _mf(
                library_id="x",
                filename="a.mkv",
                height=1080,
                video_codec="h264",
                container="matroska",
                bitrate_kbps=4000,
            ),
            _mf(
                library_id="x",
                filename="b.mkv",
                height=1080,
                video_codec="h264",
                container="matroska",
                bitrate_kbps=6000,
            ),
            _mf(
                library_id="x",
                filename="c.mkv",
                height=1080,
                video_codec="h264",
                container="matroska",
                bitrate_kbps=5000,
            ),
            # 1 file alone in (4K, h265, matroska) cell → dropped.
            _mf(
                library_id="x",
                filename="d.mkv",
                height=2160,
                video_codec="h265",
                container="matroska",
                bitrate_kbps=15000,
            ),
        ]
    )
    r = await client.get("/api/v1/dashboard/composition", headers=headers)
    body = r.json()
    matrix = body["bitrate_matrix"]
    # Exactly one kept cell (the h264/1080p/MKV one).
    assert len(matrix) == 1
    row = matrix[0]
    assert row["resolution_key"] == "1080p"
    assert row["video_codec"] == "h264"
    assert row["container"] == "MKV"
    assert row["file_count"] == 3
    # Median of {4000, 5000, 6000} = 5000.
    assert row["median_bitrate_kbps"] == 5000


@pytest.mark.asyncio
async def test_library_id_scoping(client: AsyncClient) -> None:
    """``?library_id=`` filters every section to that library."""
    headers = await _user_headers(client)
    # Two libraries; one file in each.
    async with get_database().session() as sess:
        lib_a = Library(name="a", root_path="/a", kind="movies")
        lib_b = Library(name="b", root_path="/b", kind="movies")
        sess.add_all([lib_a, lib_b])
        await sess.flush()
        sess.add_all(
            [
                _mf(library_id=lib_a.id, filename="x.mkv", height=1080),
                _mf(library_id=lib_b.id, filename="y.mkv", height=720),
            ]
        )
        await sess.commit()
        lib_a_id = lib_a.id

    # Without scoping — both files appear.
    r = await client.get("/api/v1/dashboard/composition", headers=headers)
    by_key = {row["key"]: row["count"] for row in r.json()["resolutions"]}
    assert by_key.get("1080p") == 1
    assert by_key.get("720p") == 1

    # Scoped to library A — only the 1080p file.
    r = await client.get(
        f"/api/v1/dashboard/composition?library_id={lib_a_id}",
        headers=headers,
    )
    by_key = {row["key"]: row["count"] for row in r.json()["resolutions"]}
    assert by_key.get("1080p") == 1
    assert by_key.get("720p") is None  # Filtered out.
