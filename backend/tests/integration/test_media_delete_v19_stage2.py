"""Operator-initiated media delete (v1.9 Stage 2.4).

Pins:

  1. ``DELETE /api/v1/media/{id}`` (admin) removes the row and, when
     ``remove_from_disk=true``, moves the on-disk file to a
     date-bucketed trash directory under ``data_dir/trash/``.
  2. ``POST /api/v1/media/bulk-delete`` (admin) handles a list of
     ids; unknown ids land in ``not_found`` and the call still
     succeeds for the rest.
  3. Each successful delete writes an ``AuditLogEntry`` with
     ``action="file.deleted"``, ``actor_label="operator"``, and the
     supplied reason in metadata.
  4. A ``media.deleted`` event is published per file so the WS
     bridge can drive client-side refresh.
  5. The default ``remove_from_disk=false`` mode leaves the file on
     disk completely untouched.
  6. Non-admin users cannot reach either endpoint.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.audit_log import AuditLogEntry
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
    db_path = tmp_path / "media_delete.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    monkeypatch.setenv("AUDITARR_DATA_DIR", str(data_dir))

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
            # Hand the test access to the tmp data_dir + library_root.
            c._data_dir = data_dir  # type: ignore[attr-defined]
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


async def _admin_headers(client: AsyncClient) -> tuple[dict[str, str], str]:
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "admin@example.com",
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
    return {"authorization": f"Bearer {login.json()['access_token']}"}, user_id


async def _seed_media(
    library_root: Path, *, filename: str = "movie.mkv"
) -> tuple[str, Path]:
    """Seed a library (idempotent — created once per ``library_root``)
    plus one media file. Returns ``(media_id, on-disk path)``."""
    library_root.mkdir(parents=True, exist_ok=True)
    file_path = library_root / filename
    file_path.write_bytes(b"fake media payload")
    async with get_database().session() as sess:
        # Reuse the library if it already exists in this DB (we key
        # libraries by ``root_path`` so calling ``_seed_media`` twice
        # with the same dir doesn't trip the UNIQUE-name constraint).
        existing = await sess.execute(
            select(Library).where(Library.root_path == str(library_root))
        )
        lib = existing.scalar_one_or_none()
        if lib is None:
            # Library name derived from the directory's basename so
            # two distinct ``library_root`` values get distinct names.
            lib = Library(
                name=library_root.name,
                root_path=str(library_root),
                kind="movies",
            )
            sess.add(lib)
            await sess.flush()
        mf = MediaFile(
            library_id=lib.id,
            path=str(file_path),
            relative_path=filename,
            filename=filename,
            extension="mkv",
            size_bytes=file_path.stat().st_size,
            mtime=_dt.datetime.fromtimestamp(file_path.stat().st_mtime, tz=_dt.UTC),
            category="media",
            severity="ok",
            severity_rank=10,
            has_subtitles=False,
            probe_failed=False,
            seen_at=utcnow(),
            is_orphaned=False,
        )
        sess.add(mf)
        await sess.commit()
        return mf.id, file_path


# ── Single delete ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_one_index_only_keeps_file_on_disk(
    client: AsyncClient, tmp_path: Path
) -> None:
    """Default ``remove_from_disk=false``: row gone, file untouched."""
    headers, _ = await _admin_headers(client)
    media_id, file_path = await _seed_media(tmp_path / "lib1")

    r = await client.request(
        "DELETE",
        f"/api/v1/media/{media_id}",
        headers=headers,
        json={"remove_from_disk": False, "reason": "operator test"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["media_id"] == media_id
    assert body["removed_from_disk"] is False
    assert body["trash_path"] is None
    # File still on disk.
    assert file_path.exists()
    # Row gone.
    async with get_database().session() as sess:
        result = await sess.execute(select(MediaFile).where(MediaFile.id == media_id))
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_delete_one_remove_from_disk_moves_to_dated_trash(
    client: AsyncClient, tmp_path: Path
) -> None:
    """``remove_from_disk=true``: file ends up in
    ``data_dir/trash/<YYYY-MM-DD>/<uuid>/<relative_path>`` and the
    row is removed."""
    headers, _ = await _admin_headers(client)
    media_id, file_path = await _seed_media(
        tmp_path / "lib1", filename="movie.mkv"
    )
    data_dir = client._data_dir  # type: ignore[attr-defined]
    today = _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d")

    r = await client.request(
        "DELETE",
        f"/api/v1/media/{media_id}",
        headers=headers,
        json={"remove_from_disk": True, "reason": "operator test"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["removed_from_disk"] is True
    assert body["trash_path"] is not None
    trash_path = Path(body["trash_path"])
    # Trash path lives under data_dir/trash/<today>/<uuid>/...
    assert trash_path.exists()
    assert not file_path.exists()
    rel = trash_path.relative_to(data_dir / "trash")
    parts = rel.parts
    assert parts[0] == today
    # Second part is a uuid bucket; we don't pin the exact value
    # but it must be non-empty.
    assert parts[1]
    # Final part preserves the original filename / relative path.
    assert parts[-1] == "movie.mkv"


@pytest.mark.asyncio
async def test_delete_one_writes_audit_log(
    client: AsyncClient, tmp_path: Path
) -> None:
    """Every operator delete writes an audit-log entry tagged
    ``file.deleted`` with ``actor_label="operator"`` and the
    operator's user_id."""
    headers, admin_id = await _admin_headers(client)
    media_id, _ = await _seed_media(tmp_path / "lib1")

    r = await client.request(
        "DELETE",
        f"/api/v1/media/{media_id}",
        headers=headers,
        json={"remove_from_disk": False, "reason": "audit test"},
    )
    assert r.status_code == 200, r.text

    async with get_database().session() as sess:
        result = await sess.execute(
            select(AuditLogEntry).where(
                AuditLogEntry.action == "file.deleted"
            )
        )
        rows = list(result.scalars().all())
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_id == admin_id
    assert row.actor_label == "operator"
    assert row.target_type == "media_file"
    assert row.target_id == media_id
    assert row.metadata_["reason"] == "audit test"
    assert row.metadata_["remove_from_disk"] is False


@pytest.mark.asyncio
async def test_delete_one_emits_media_deleted_event(
    client: AsyncClient, tmp_path: Path
) -> None:
    """The event bus sees one ``media.deleted`` with the row's id."""
    headers, _ = await _admin_headers(client)
    media_id, _ = await _seed_media(tmp_path / "lib1")

    bus = get_event_bus()
    seen: list[dict] = []

    async def listener(event) -> None:
        seen.append(
            {"name": event.name, "id": event.payload.get("id")}
        )

    bus.subscribe("media.deleted", listener)
    r = await client.request(
        "DELETE",
        f"/api/v1/media/{media_id}",
        headers=headers,
        json={"remove_from_disk": False, "reason": None},
    )
    assert r.status_code == 200, r.text
    assert any(
        e["name"] == "media.deleted" and e["id"] == media_id for e in seen
    )


@pytest.mark.asyncio
async def test_delete_one_404s_for_unknown_id(client: AsyncClient) -> None:
    headers, _ = await _admin_headers(client)
    r = await client.request(
        "DELETE",
        "/api/v1/media/no-such-id",
        headers=headers,
        json={"remove_from_disk": False, "reason": None},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_one_requires_admin(
    client: AsyncClient, tmp_path: Path
) -> None:
    """A non-admin can't delete."""
    media_id, _ = await _seed_media(tmp_path / "lib1")
    # Register a non-admin user and use their token.
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
    headers = {"authorization": f"Bearer {login.json()['access_token']}"}
    r = await client.request(
        "DELETE",
        f"/api/v1/media/{media_id}",
        headers=headers,
        json={"remove_from_disk": False, "reason": None},
    )
    assert r.status_code in (401, 403)


# ── Bulk delete ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_delete_handles_known_and_unknown_ids(
    client: AsyncClient, tmp_path: Path
) -> None:
    """Mix of known + unknown ids → only known are deleted; unknown
    surface in ``not_found``; call still succeeds."""
    headers, _ = await _admin_headers(client)
    id1, _ = await _seed_media(tmp_path / "lib1", filename="a.mkv")
    id2, _ = await _seed_media(tmp_path / "lib1", filename="b.mkv")

    r = await client.post(
        "/api/v1/media/bulk-delete",
        headers=headers,
        json={
            "ids": [id1, id2, "ghost-id"],
            "remove_from_disk": False,
            "reason": "spring cleaning",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["requested"] == 3
    deleted_ids = {d["media_id"] for d in body["deleted"]}
    assert deleted_ids == {id1, id2}
    assert body["not_found"] == ["ghost-id"]


@pytest.mark.asyncio
async def test_bulk_delete_rejects_duplicates(
    client: AsyncClient, tmp_path: Path
) -> None:
    headers, _ = await _admin_headers(client)
    id1, _ = await _seed_media(tmp_path / "lib1", filename="a.mkv")

    r = await client.post(
        "/api/v1/media/bulk-delete",
        headers=headers,
        json={"ids": [id1, id1], "remove_from_disk": False, "reason": None},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_bulk_delete_remove_from_disk_shares_one_bucket(
    client: AsyncClient, tmp_path: Path
) -> None:
    """All files in one bulk call land in the SAME trash bucket
    (date + uuid pair), so the operator can restore the whole batch
    by moving one directory."""
    headers, _ = await _admin_headers(client)
    id1, _ = await _seed_media(tmp_path / "lib1", filename="a.mkv")
    id2, _ = await _seed_media(tmp_path / "lib1", filename="b.mkv")

    r = await client.post(
        "/api/v1/media/bulk-delete",
        headers=headers,
        json={
            "ids": [id1, id2],
            "remove_from_disk": True,
            "reason": "bulk test",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    paths = [Path(d["trash_path"]) for d in body["deleted"]]
    # Both files share the same parent directory (the bucket).
    assert len({p.parent for p in paths}) == 1


@pytest.mark.asyncio
async def test_bulk_delete_writes_one_audit_row_per_file(
    client: AsyncClient, tmp_path: Path
) -> None:
    headers, _ = await _admin_headers(client)
    id1, _ = await _seed_media(tmp_path / "lib1", filename="a.mkv")
    id2, _ = await _seed_media(tmp_path / "lib1", filename="b.mkv")

    r = await client.post(
        "/api/v1/media/bulk-delete",
        headers=headers,
        json={
            "ids": [id1, id2],
            "remove_from_disk": False,
            "reason": "audit bulk",
        },
    )
    assert r.status_code == 200, r.text

    async with get_database().session() as sess:
        result = await sess.execute(
            select(AuditLogEntry).where(
                AuditLogEntry.action == "file.deleted"
            )
        )
        rows = list(result.scalars().all())
    assert len(rows) == 2
    target_ids = {r.target_id for r in rows}
    assert target_ids == {id1, id2}


@pytest.mark.asyncio
async def test_bulk_delete_requires_admin(
    client: AsyncClient, tmp_path: Path
) -> None:
    id1, _ = await _seed_media(tmp_path / "lib1", filename="a.mkv")
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
    headers = {"authorization": f"Bearer {login.json()['access_token']}"}
    r = await client.post(
        "/api/v1/media/bulk-delete",
        headers=headers,
        json={"ids": [id1], "remove_from_disk": False, "reason": None},
    )
    assert r.status_code in (401, 403)
