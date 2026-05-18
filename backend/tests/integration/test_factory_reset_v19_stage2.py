"""Factory reset (v1.9 Stage 2.6).

Pins:

  1. ``POST /api/v1/system/factory-reset`` (admin) with the right
     ``confirm_phrase`` truncates application tables, leaves the
     ``users`` and ``audit_log`` tables intact, and writes a
     ``factory_reset`` audit entry.
  2. The trash directory under ``data_dir/trash/`` is purged.
  3. A wrong ``confirm_phrase`` returns 422 and changes nothing.
  4. Non-admin callers are refused (401/403).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.audit_log import AuditLogEntry
from app.models.library import Library
from app.models.user import User
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "factory_reset.db"
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


async def _seed_application_data(library_root: Path) -> None:
    """Seed enough rows that the wipe has something to remove."""
    library_root.mkdir(parents=True, exist_ok=True)
    async with get_database().session() as sess:
        sess.add(
            Library(
                name="movies",
                root_path=str(library_root),
                kind="movies",
            )
        )
        sess.add(
            Library(
                name="tv",
                root_path=str(library_root) + "_tv",
                kind="tv",
            )
        )
        await sess.commit()


@pytest.mark.asyncio
async def test_factory_reset_truncates_applications_tables(
    client: AsyncClient, tmp_path: Path
) -> None:
    """After a successful reset, application tables are empty."""
    headers, _ = await _admin_headers(client)
    await _seed_application_data(tmp_path / "lib")

    # Pre-check: libraries seeded.
    async with get_database().session() as sess:
        count = (await sess.execute(select(func.count(Library.id)))).scalar()
    assert count == 2

    r = await client.post(
        "/api/v1/system/factory-reset",
        headers=headers,
        json={"confirm_phrase": "reset auditarr"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tables_truncated"] > 0

    async with get_database().session() as sess:
        count = (await sess.execute(select(func.count(Library.id)))).scalar()
    assert count == 0


@pytest.mark.asyncio
async def test_factory_reset_preserves_users_table(
    client: AsyncClient,
) -> None:
    """The admin must still exist (and remain admin) after reset."""
    headers, admin_id = await _admin_headers(client)

    r = await client.post(
        "/api/v1/system/factory-reset",
        headers=headers,
        json={"confirm_phrase": "reset auditarr"},
    )
    assert r.status_code == 200, r.text

    async with get_database().session() as sess:
        users = list(
            (await sess.execute(select(User))).scalars().all()
        )
    assert len(users) == 1
    assert users[0].id == admin_id
    assert users[0].role == "admin"


@pytest.mark.asyncio
async def test_factory_reset_writes_audit_entry(
    client: AsyncClient,
) -> None:
    """The reset is itself audited — the row survives because
    ``audit_log`` is on the preserved list."""
    headers, admin_id = await _admin_headers(client)

    r = await client.post(
        "/api/v1/system/factory-reset",
        headers=headers,
        json={"confirm_phrase": "reset auditarr"},
    )
    assert r.status_code == 200, r.text

    async with get_database().session() as sess:
        rows = list(
            (
                await sess.execute(
                    select(AuditLogEntry).where(
                        AuditLogEntry.action == "factory_reset"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    row = rows[0]
    assert row.actor_id == admin_id
    assert row.actor_label == "operator"
    assert row.metadata_["confirm_phrase"] == "reset auditarr"
    assert row.metadata_["tables_truncated"] > 0


@pytest.mark.asyncio
async def test_factory_reset_purges_trash_directory(
    client: AsyncClient, tmp_path: Path
) -> None:
    """Files under ``data_dir/trash/`` are removed."""
    headers, _ = await _admin_headers(client)
    data_dir = client._data_dir  # type: ignore[attr-defined]
    trash_dir = data_dir / "trash" / "2026-05-17" / "bucket-abc"
    trash_dir.mkdir(parents=True)
    (trash_dir / "old.mkv").write_bytes(b"trashed content")

    r = await client.post(
        "/api/v1/system/factory-reset",
        headers=headers,
        json={"confirm_phrase": "reset auditarr"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["trash_purged"] is True
    # Trash file is gone.
    assert not (trash_dir / "old.mkv").exists()
    # Trash root recreated empty so subsequent operator-deletes
    # don't have to mkdir from scratch.
    assert (data_dir / "trash").exists()
    assert (data_dir / "trash").is_dir()
    # Empty.
    assert list((data_dir / "trash").iterdir()) == []


@pytest.mark.asyncio
async def test_factory_reset_wrong_phrase_returns_422_and_does_nothing(
    client: AsyncClient, tmp_path: Path
) -> None:
    headers, _ = await _admin_headers(client)
    await _seed_application_data(tmp_path / "lib")

    r = await client.post(
        "/api/v1/system/factory-reset",
        headers=headers,
        json={"confirm_phrase": "delete everything"},
    )
    assert r.status_code == 422

    # Application data still intact.
    async with get_database().session() as sess:
        count = (await sess.execute(select(func.count(Library.id)))).scalar()
    assert count == 2


@pytest.mark.asyncio
async def test_factory_reset_requires_admin(client: AsyncClient) -> None:
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
        "/api/v1/system/factory-reset",
        headers=headers,
        json={"confirm_phrase": "reset auditarr"},
    )
    assert r.status_code in (401, 403)
