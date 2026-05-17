"""v1.8.1 — stale-scan reaper + manual reset endpoint tests.

Pins the contract for the two new defenses against stuck scans:

  * ``reap_stale_scans`` worker tick — marks ``queued``/``running``
    ScanRun rows that sit unchanged for >1 hour as ``failed``.
    Triggered by worker crashes (OOM, SIGKILL, container restart)
    that prevent the in-process exception handler from running.
  * ``POST /scans/libraries/{id}/reset`` admin endpoint — manual
    unstick for operators who don't want to wait the full hour.

Why we care: pre-v1.8.1, a single mid-scan worker crash would
permanently block all future scans of that library, because the
``find_active_for_library`` guard treats any ``queued``/``running``
row as evidence that a scan is in flight. The fix is two-pronged:
the reaper handles the slow case (eventually unstick); the manual
endpoint handles the fast case (operator wants it now).
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.library import Library
from app.models.scan_run import ScanRun
from app.models.user import User
from app.services.repositories import LibraryRepository
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database
from app.utils.datetime import utcnow
from app.worker import reap_stale_scans

PASSWORD = "supersecret-password-1!"


# ── Fixtures ──────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncSession]:
    """Bare DB session + schema. No HTTP."""
    db_path = tmp_path / "reaper.db"
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

    sess = db._sessionmaker()  # type: ignore[misc]
    try:
        yield sess
    finally:
        await sess.close()
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        get_settings.cache_clear()


@pytest_asyncio.fixture
async def http_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    """ASGI test client + DB + admin auth."""
    db_path = tmp_path / "reaper_api.db"
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
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "admin@example.com",
            "username": "admin",
            "password": PASSWORD,
        },
    )
    user = r.json()
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


async def _seed_library(tmp_path: Path) -> str:
    root = tmp_path / "lib"
    root.mkdir(exist_ok=True)
    async with get_database().session() as sess:
        lib = Library(
            name="movies",
            root_path=str(root),
            kind="movies",
            enabled=True,
        )
        await LibraryRepository(sess).add(lib)
        await sess.commit()
        return lib.id


# ── reap_stale_scans behaviour ────────────────────────────────


@pytest.mark.asyncio
async def test_reaper_marks_old_running_row_failed(
    db_session: AsyncSession,
) -> None:
    """A ``running`` ScanRun row whose ``started_at`` is >1 hour
    ago is marked ``failed`` by the reaper."""
    library = Library(
        name="m", root_path="/tmp/x", kind="movies", enabled=True
    )
    await LibraryRepository(db_session).add(library)
    await db_session.commit()

    # Create a "stuck" running scan.
    old = utcnow() - _dt.timedelta(hours=2)
    run = ScanRun(
        library_id=library.id,
        mode="full",
        status="running",
        started_at=old,
    )
    db_session.add(run)
    await db_session.commit()
    run_id = run.id

    # Invoke the reaper with a synthetic ctx that mirrors what
    # ARQ would pass.
    db = get_database()
    bus = get_event_bus()
    bus.clear()
    result = await reap_stale_scans({"db": db, "bus": bus})

    assert result["reaped"] == 1
    assert run_id in result["run_ids"]

    # Verify in the DB.
    async with db.session() as sess:
        row = (
            await sess.execute(select(ScanRun).where(ScanRun.id == run_id))
        ).scalar_one()
    assert row.status == "failed"
    assert row.error is not None
    assert "stale-scan watchdog" in row.error
    assert row.finished_at is not None


@pytest.mark.asyncio
async def test_reaper_marks_old_queued_row_failed(
    db_session: AsyncSession,
) -> None:
    """A ``queued`` row that never started (started_at is NULL)
    gets reaped via the ``created_at`` fallback."""
    library = Library(
        name="m", root_path="/tmp/x", kind="movies", enabled=True
    )
    await LibraryRepository(db_session).add(library)
    await db_session.commit()

    # SQLite doesn't honour ``server_default=func.now()`` for our
    # explicit ``started_at=None`` insert, so we'll set it
    # directly to NULL and back-date created_at.
    old = utcnow() - _dt.timedelta(hours=2)
    run = ScanRun(
        library_id=library.id,
        mode="full",
        status="queued",
        created_at=old,
        started_at=None,
    )
    db_session.add(run)
    await db_session.commit()
    run_id = run.id

    db = get_database()
    bus = get_event_bus()
    bus.clear()
    result = await reap_stale_scans({"db": db, "bus": bus})

    assert result["reaped"] == 1
    async with db.session() as sess:
        row = (
            await sess.execute(select(ScanRun).where(ScanRun.id == run_id))
        ).scalar_one()
    assert row.status == "failed"


@pytest.mark.asyncio
async def test_reaper_leaves_recent_rows_alone(
    db_session: AsyncSession,
) -> None:
    """A scan started 10 minutes ago is well under the 1-hour
    threshold and must NOT be reaped — it's probably still
    making progress."""
    library = Library(
        name="m", root_path="/tmp/x", kind="movies", enabled=True
    )
    await LibraryRepository(db_session).add(library)
    await db_session.commit()

    recent = utcnow() - _dt.timedelta(minutes=10)
    run = ScanRun(
        library_id=library.id,
        mode="full",
        status="running",
        started_at=recent,
    )
    db_session.add(run)
    await db_session.commit()
    run_id = run.id

    db = get_database()
    bus = get_event_bus()
    bus.clear()
    result = await reap_stale_scans({"db": db, "bus": bus})

    assert result["reaped"] == 0
    async with db.session() as sess:
        row = (
            await sess.execute(select(ScanRun).where(ScanRun.id == run_id))
        ).scalar_one()
    # Still running.
    assert row.status == "running"
    assert row.finished_at is None


@pytest.mark.asyncio
async def test_reaper_leaves_completed_rows_alone(
    db_session: AsyncSession,
) -> None:
    """A 2-hour-old ``completed`` row must NOT be reaped — it's
    in a terminal state. The reaper only touches stuck
    queued/running rows."""
    library = Library(
        name="m", root_path="/tmp/x", kind="movies", enabled=True
    )
    await LibraryRepository(db_session).add(library)
    await db_session.commit()

    old = utcnow() - _dt.timedelta(hours=2)
    run = ScanRun(
        library_id=library.id,
        mode="full",
        status="completed",
        started_at=old,
        finished_at=old + _dt.timedelta(minutes=30),
    )
    db_session.add(run)
    await db_session.commit()
    run_id = run.id

    db = get_database()
    bus = get_event_bus()
    bus.clear()
    result = await reap_stale_scans({"db": db, "bus": bus})

    assert result["reaped"] == 0
    async with db.session() as sess:
        row = (
            await sess.execute(select(ScanRun).where(ScanRun.id == run_id))
        ).scalar_one()
    assert row.status == "completed"


@pytest.mark.asyncio
async def test_reaper_emits_scan_reaped_event(
    db_session: AsyncSession,
) -> None:
    """For every reaped run the reaper emits a ``scan.reaped``
    event so the UI WS subscribers can refresh."""
    library = Library(
        name="m", root_path="/tmp/x", kind="movies", enabled=True
    )
    await LibraryRepository(db_session).add(library)
    await db_session.commit()

    old = utcnow() - _dt.timedelta(hours=2)
    run = ScanRun(
        library_id=library.id,
        mode="full",
        status="running",
        started_at=old,
    )
    db_session.add(run)
    await db_session.commit()
    run_id = run.id

    db = get_database()
    bus = get_event_bus()
    bus.clear()
    received: list[dict[str, object]] = []
    bus.subscribe(
        "scan.reaped",
        lambda evt: received.append(dict(getattr(evt, "payload", {}))),
    )

    await reap_stale_scans({"db": db, "bus": bus})

    assert len(received) == 1
    assert received[0]["run_id"] == run_id
    assert received[0]["library_id"] == library.id
    assert received[0]["age_seconds"] >= 3600


# ── Manual reset endpoint ─────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_endpoint_clears_queued_and_running(
    http_client: AsyncClient, tmp_path: Path
) -> None:
    headers = await _admin_headers(http_client)
    library_id = await _seed_library(tmp_path)

    # Stack up two stuck scans — one queued, one running.
    async with get_database().session() as sess:
        sess.add(
            ScanRun(
                library_id=library_id,
                mode="full",
                status="queued",
            )
        )
        sess.add(
            ScanRun(
                library_id=library_id,
                mode="full",
                status="running",
                started_at=utcnow(),
            )
        )
        await sess.commit()

    r = await http_client.post(
        f"/api/v1/scans/libraries/{library_id}/reset",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reset_count"] == 2
    assert len(body["run_ids"]) == 2

    # Verify both are failed now.
    async with get_database().session() as sess:
        rows = (
            await sess.execute(
                select(ScanRun).where(ScanRun.library_id == library_id)
            )
        ).scalars().all()
    assert all(r.status == "failed" for r in rows)
    assert all("Manually reset by operator" in (r.error or "") for r in rows)


@pytest.mark.asyncio
async def test_reset_endpoint_no_op_when_nothing_stuck(
    http_client: AsyncClient, tmp_path: Path
) -> None:
    """No queued/running rows → 200 with reset_count=0. Idempotent
    enough that the frontend can fire it speculatively."""
    headers = await _admin_headers(http_client)
    library_id = await _seed_library(tmp_path)

    r = await http_client.post(
        f"/api/v1/scans/libraries/{library_id}/reset",
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reset_count"] == 0
    assert body["run_ids"] == []


@pytest.mark.asyncio
async def test_reset_endpoint_404_for_unknown_library(
    http_client: AsyncClient,
) -> None:
    headers = await _admin_headers(http_client)
    r = await http_client.post(
        "/api/v1/scans/libraries/does-not-exist/reset",
        headers=headers,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_reset_endpoint_requires_admin(
    http_client: AsyncClient, tmp_path: Path
) -> None:
    """A non-admin user gets 403; the reset endpoint is admin-only
    because it forcibly mutates server state."""
    # Register a non-admin user.
    r = await http_client.post(
        "/api/v1/auth/register",
        json={
            "email": "user@example.com",
            "username": "user",
            "password": PASSWORD,
        },
    )
    assert r.status_code in (200, 201)
    login = await http_client.post(
        "/api/v1/auth/login",
        json={"login": "user", "password": PASSWORD},
    )
    headers = {"authorization": f"Bearer {login.json()['access_token']}"}
    library_id = await _seed_library(tmp_path)

    r = await http_client.post(
        f"/api/v1/scans/libraries/{library_id}/reset",
        headers=headers,
    )
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_reset_endpoint_leaves_completed_rows_alone(
    http_client: AsyncClient, tmp_path: Path
) -> None:
    """A completed row in the same library must NOT be touched —
    only queued/running rows are stuck. We don't want to rewrite
    history."""
    headers = await _admin_headers(http_client)
    library_id = await _seed_library(tmp_path)

    async with get_database().session() as sess:
        completed = ScanRun(
            library_id=library_id,
            mode="full",
            status="completed",
            started_at=utcnow() - _dt.timedelta(hours=1),
            finished_at=utcnow() - _dt.timedelta(minutes=30),
        )
        sess.add(completed)
        await sess.commit()
        completed_id = completed.id

    r = await http_client.post(
        f"/api/v1/scans/libraries/{library_id}/reset",
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["reset_count"] == 0

    async with get_database().session() as sess:
        row = (
            await sess.execute(
                select(ScanRun).where(ScanRun.id == completed_id)
            )
        ).scalar_one()
    assert row.status == "completed"
