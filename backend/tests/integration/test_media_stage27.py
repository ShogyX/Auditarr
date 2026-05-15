"""Stage 27 — Reprobe + Quarantine API.

Covers the new endpoints:

  - POST /api/v1/media/{id}/reprobe (admin-only)
  - POST /api/v1/media/{id}/quarantine (admin-only)
  - POST /api/v1/media/{id}/unquarantine (admin-only)
  - POST /api/v1/media/bulk/reprobe (admin-only)
  - POST /api/v1/media/bulk/quarantine (admin-only)
  - POST /api/v1/media/bulk/unquarantine (admin-only)
  - GET  /api/v1/media excludes quarantined files by default,
    but honors ``quarantined=true`` / ``include_quarantined=true``

The reprobe endpoint substitutes an in-process Ffprobe stub so
the test suite doesn't need the binary installed. The substitution
patches the module-level singleton via
``app.services.media.ffprobe.reset_ffprobe_service``.
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
from app.services.media import FfprobeResult
from app.services.media.ffprobe import reset_ffprobe_service
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database
from app.utils.datetime import utcnow

PASSWORD = "supersecret-password-1!"


class StubFfprobe:
    """In-process FfprobeService substitute for the API tests."""

    def __init__(self, results: dict[str, FfprobeResult] | None = None) -> None:
        self._results = results or {}
        self.calls: list[str] = []

    @property
    def is_available(self) -> bool:
        return True

    async def probe(self, path: str) -> FfprobeResult:
        self.calls.append(path)
        return self._results.get(
            path,
            FfprobeResult(
                ok=True,
                container="matroska",
                video_codec="av1",
                audio_codec="aac",
            ),
        )


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "media_stage27.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )

    from app.core.settings import get_settings

    get_settings.cache_clear()

    # Replace the module-level Ffprobe singleton with our stub. The
    # API constructs a Scanner per-request via get_ffprobe_service(),
    # so monkeypatching the factory function is the cleanest way to
    # inject the stub.
    stub = StubFfprobe()
    monkeypatch.setattr(
        "app.services.media.ffprobe._service", stub, raising=False
    )
    monkeypatch.setattr(
        "app.api.v1.media.get_ffprobe_service", lambda: stub
    )

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
            c._stub = stub  # type: ignore[attr-defined]
            c._tmp_path = tmp_path  # type: ignore[attr-defined]
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
        reset_ffprobe_service()
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


async def _user_headers(client: AsyncClient) -> dict[str, str]:
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


async def _seed_one_file(client: AsyncClient, *, exists: bool = True) -> str:
    """Insert a library + a single media file, return the media id.

    If ``exists`` is False, the media row's ``path`` points to a
    non-existent file on disk — used to exercise the orphan branch.
    """
    tmp_path: Path = client._tmp_path  # type: ignore[attr-defined]
    file_path = tmp_path / "movie.mkv"
    if exists:
        file_path.write_bytes(b"x" * 200)

    async with get_database().session() as sess:
        library = Library(name="L", root_path=str(tmp_path), kind="movies")
        sess.add(library)
        await sess.flush()

        mf = MediaFile(
            library_id=library.id,
            path=str(file_path),
            relative_path="movie.mkv",
            filename="movie.mkv",
            extension="mkv",
            size_bytes=200,
            mtime=utcnow(),
            category="media",
            severity="ok",
            severity_rank=10,
            container="mp4",  # stale data we expect reprobe to overwrite
            video_codec="hevc",
            has_subtitles=False,
            seen_at=utcnow(),
            is_orphaned=False,
        )
        sess.add(mf)
        await sess.commit()
        return mf.id


# ── Reprobe ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reprobe_endpoint_updates_probe_columns(
    client: AsyncClient,
) -> None:
    media_id = await _seed_one_file(client)
    headers = await _admin_headers(client)

    response = await client.post(
        f"/api/v1/media/{media_id}/reprobe", headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Stub returns container=matroska, video_codec=av1 (default).
    assert body["container"] == "matroska"
    assert body["video_codec"] == "av1"
    assert body["probe_failed"] is False


@pytest.mark.asyncio
async def test_reprobe_endpoint_admin_only(client: AsyncClient) -> None:
    media_id = await _seed_one_file(client)
    user_headers = await _user_headers(client)

    response = await client.post(
        f"/api/v1/media/{media_id}/reprobe", headers=user_headers
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_reprobe_endpoint_404_for_unknown_id(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/media/nonexistent-id/reprobe", headers=headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_reprobe_endpoint_orphan_branch(client: AsyncClient) -> None:
    """When the file path is gone from disk, the row is marked
    orphaned but the endpoint still returns 200."""
    media_id = await _seed_one_file(client, exists=False)
    headers = await _admin_headers(client)

    response = await client.post(
        f"/api/v1/media/{media_id}/reprobe", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["is_orphaned"] is True


@pytest.mark.asyncio
async def test_bulk_reprobe(client: AsyncClient) -> None:
    media_id_a = await _seed_one_file(client)
    # Make sure the seed produces two rows: hack a second one by
    # editing the path.
    tmp_path: Path = client._tmp_path  # type: ignore[attr-defined]
    other = tmp_path / "other.mkv"
    other.write_bytes(b"y" * 200)
    async with get_database().session() as sess:
        async with sess.begin():
            library_id = (await sess.execute(
                __import__("sqlalchemy").select(Library.id).limit(1)
            )).scalar_one()
            mf = MediaFile(
                library_id=library_id,
                path=str(other),
                relative_path="other.mkv",
                filename="other.mkv",
                extension="mkv",
                size_bytes=200,
                mtime=utcnow(),
                category="media",
                severity="ok",
                severity_rank=10,
                has_subtitles=False,
                seen_at=utcnow(),
                is_orphaned=False,
            )
            sess.add(mf)
        media_id_b = mf.id

    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/media/bulk/reprobe",
        headers=headers,
        json={"media_ids": [media_id_a, media_id_b]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["files_reprobed"] == 2
    assert body["files_failed"] == 0
    assert body["files_orphaned"] == 0
    assert body["files_not_found"] == []


@pytest.mark.asyncio
async def test_bulk_reprobe_partial_unknown_ids(client: AsyncClient) -> None:
    media_id = await _seed_one_file(client)
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/media/bulk/reprobe",
        headers=headers,
        json={"media_ids": [media_id, "missing-id"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["files_reprobed"] == 1
    assert body["files_not_found"] == ["missing-id"]


# ── Quarantine ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_quarantine_endpoint(client: AsyncClient) -> None:
    media_id = await _seed_one_file(client)
    headers = await _admin_headers(client)

    response = await client.post(
        f"/api/v1/media/{media_id}/quarantine",
        headers=headers,
        json={"reason": "Broken on disk; can't decode"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["quarantined"] is True
    assert body["quarantined_reason"] == "Broken on disk; can't decode"
    assert body["quarantined_at"] is not None


@pytest.mark.asyncio
async def test_quarantine_is_idempotent(client: AsyncClient) -> None:
    """Quarantining an already-quarantined file refreshes the
    timestamp + reason rather than erroring."""
    media_id = await _seed_one_file(client)
    headers = await _admin_headers(client)

    first = await client.post(
        f"/api/v1/media/{media_id}/quarantine",
        headers=headers,
        json={"reason": "first reason"},
    )
    assert first.status_code == 200

    second = await client.post(
        f"/api/v1/media/{media_id}/quarantine",
        headers=headers,
        json={"reason": "second reason"},
    )
    assert second.status_code == 200
    assert second.json()["quarantined_reason"] == "second reason"


@pytest.mark.asyncio
async def test_unquarantine_endpoint(client: AsyncClient) -> None:
    media_id = await _seed_one_file(client)
    headers = await _admin_headers(client)

    await client.post(
        f"/api/v1/media/{media_id}/quarantine",
        headers=headers,
        json={"reason": "test"},
    )
    response = await client.post(
        f"/api/v1/media/{media_id}/unquarantine", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["quarantined"] is False
    assert body["quarantined_at"] is None
    assert body["quarantined_reason"] is None


@pytest.mark.asyncio
async def test_quarantine_admin_only(client: AsyncClient) -> None:
    media_id = await _seed_one_file(client)
    user_headers = await _user_headers(client)
    response = await client.post(
        f"/api/v1/media/{media_id}/quarantine",
        headers=user_headers,
        json={"reason": "test"},
    )
    assert response.status_code == 403


# ── List endpoint quarantine filter ─────────────────────────


@pytest.mark.asyncio
async def test_list_excludes_quarantined_by_default(
    client: AsyncClient,
) -> None:
    media_id = await _seed_one_file(client)
    headers = await _admin_headers(client)

    # Quarantine the file.
    await client.post(
        f"/api/v1/media/{media_id}/quarantine",
        headers=headers,
        json={"reason": "for the test"},
    )

    # Default list should NOT include it.
    response = await client.get("/api/v1/media", headers=headers)
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["items"]]
    assert media_id not in ids


@pytest.mark.asyncio
async def test_list_returns_quarantined_when_explicit(
    client: AsyncClient,
) -> None:
    media_id = await _seed_one_file(client)
    headers = await _admin_headers(client)

    await client.post(
        f"/api/v1/media/{media_id}/quarantine",
        headers=headers,
        json={"reason": "for the test"},
    )

    # Explicit quarantined=true returns only quarantined files.
    response = await client.get(
        "/api/v1/media?quarantined=true", headers=headers
    )
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["items"]]
    assert media_id in ids


@pytest.mark.asyncio
async def test_list_include_quarantined_mixes_both(
    client: AsyncClient,
) -> None:
    """``include_quarantined=true`` returns both quarantined and
    non-quarantined files together (useful for raw-data export
    style queries)."""
    media_id = await _seed_one_file(client)
    headers = await _admin_headers(client)

    await client.post(
        f"/api/v1/media/{media_id}/quarantine",
        headers=headers,
        json={"reason": "test"},
    )

    response = await client.get(
        "/api/v1/media?include_quarantined=true", headers=headers
    )
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["items"]]
    # The quarantined file IS included.
    assert media_id in ids


# ── Bulk quarantine ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_quarantine_and_unquarantine(client: AsyncClient) -> None:
    media_id = await _seed_one_file(client)
    headers = await _admin_headers(client)

    q_res = await client.post(
        "/api/v1/media/bulk/quarantine",
        headers=headers,
        json={"media_ids": [media_id], "reason": "in bulk"},
    )
    assert q_res.status_code == 200
    assert q_res.json()["files_quarantined"] == 1

    uq_res = await client.post(
        "/api/v1/media/bulk/unquarantine",
        headers=headers,
        json={"media_ids": [media_id]},
    )
    assert uq_res.status_code == 200
    assert uq_res.json()["files_unquarantined"] == 1


@pytest.mark.asyncio
async def test_bulk_reprobe_rejects_duplicates(client: AsyncClient) -> None:
    media_id = await _seed_one_file(client)
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/media/bulk/reprobe",
        headers=headers,
        json={"media_ids": [media_id, media_id]},
    )
    # The duplicate-detection raises ``ValidationError`` which the
    # exception handler maps to 422 (matches the Stage 23 bulk
    # re-evaluate endpoint's behavior on duplicates).
    assert response.status_code == 422
