"""Stage 8 (audit follow-up) — scanner progress + scan-all + async default.

Pins three contracts:

  1. The scanner emits ``scan.progress`` events at the right cadence:
     once after enumerate (with files_seen=0 + total estimate) and
     then every 100 files, plus a final flush so the bar lands at
     100% even when the total isn't a multiple of 100.
  2. ``POST /api/v1/scans/all`` enqueues one job per enabled library.
     Disabled libraries are skipped; libraries with an active scan
     are skipped (no 409 — bulk mode shouldn't fail because one
     library is busy).
  3. The per-library trigger defaults to async (``enqueue=true``) so
     a long scan doesn't kill the API worker via gunicorn's hard
     timeout.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.bus import EventBus, get_event_bus
from app.main import create_app
from app.models.library import Library
from app.models.scan_run import ScanRun
from app.models.user import User
from app.services.media import FfprobeResult
from app.services.media.scanner import ScanOptions, Scanner
from app.services.repositories import LibraryRepository
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database
from app.utils.datetime import utcnow

PASSWORD = "supersecret-password-1!"


# ── Stub ffprobe (matches existing test_scanner.py pattern) ─────
class StubFfprobe:
    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def is_available(self) -> bool:
        return True

    async def probe(self, path: str) -> FfprobeResult:
        self.calls.append(path)
        return FfprobeResult(ok=True, container="matroska", video_codec="h264")


# ── Direct scanner test — scan.progress cadence ─────────────────
@pytest_asyncio.fixture
async def session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncSession]:
    db_path = tmp_path / "stage8.db"
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


def _seed_files(root: Path, count: int) -> None:
    """Seed ``count`` files under ``root``."""
    (root / "lib").mkdir()
    for i in range(count):
        # All ``.mkv`` so they're classified as media (the cheapest
        # path through the scanner) but the StubFfprobe doesn't fail.
        (root / "lib" / f"file_{i:04d}.mkv").write_bytes(b"x" * 10)


@pytest.mark.asyncio
async def test_scan_progress_emitted_at_start_and_every_100(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A 250-file scan should emit 5 progress events: one initial
    (files_seen=0), and one at 100/200/250 (the final flush). The
    threshold is exactly 100 today; if Stage 8 ever tunes it, this
    test needs the same constant."""
    library_root = tmp_path / "library"
    library_root.mkdir()
    _seed_files(library_root, count=250)

    library = Library(
        name="movies", root_path=str(library_root / "lib"), kind="movies"
    )
    await LibraryRepository(session).add(library)
    await session.commit()

    bus = EventBus()
    bus.clear()
    progress_events: list[dict[str, object]] = []

    def on_progress(event: object) -> None:
        # ``event`` is a DomainEvent; payload is the dict we shipped.
        progress_events.append(dict(getattr(event, "payload", {})))

    bus.subscribe("scan.progress", on_progress)
    scanner = Scanner(
        session=session,
        event_bus=bus,
        ffprobe=StubFfprobe(),  # type: ignore[arg-type]
    )
    await scanner.scan(library, options=ScanOptions(mode="full"))

    # Expected: initial (files_seen=0), then at 100, 200, 250 (final).
    assert len(progress_events) >= 4
    # First emit: zero progress, total estimate set.
    assert progress_events[0]["files_seen"] == 0
    assert progress_events[0]["files_total_estimate"] == 250
    # Each subsequent emit has a non-decreasing files_seen.
    seens = [e["files_seen"] for e in progress_events]
    assert seens == sorted(seens), "files_seen must be monotonically non-decreasing"
    # The last emit should reflect the final count.
    assert progress_events[-1]["files_seen"] == 250
    assert progress_events[-1]["files_total_estimate"] == 250


@pytest.mark.asyncio
async def test_scan_progress_small_library_emits_initial_and_final(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A 5-file scan (< 100) emits only the initial event (seen=0)
    and the final flush. No intermediate emits at the modulo
    boundary."""
    library_root = tmp_path / "library"
    library_root.mkdir()
    _seed_files(library_root, count=5)
    library = Library(
        name="movies", root_path=str(library_root / "lib"), kind="movies"
    )
    await LibraryRepository(session).add(library)
    await session.commit()

    bus = EventBus()
    bus.clear()
    events: list[dict[str, object]] = []
    bus.subscribe(
        "scan.progress", lambda e: events.append(dict(getattr(e, "payload", {}))),
    )
    scanner = Scanner(
        session=session,
        event_bus=bus,
        ffprobe=StubFfprobe(),  # type: ignore[arg-type]
    )
    await scanner.scan(library, options=ScanOptions(mode="full"))

    assert len(events) == 2
    assert events[0]["files_seen"] == 0
    assert events[1]["files_seen"] == 5


@pytest.mark.asyncio
async def test_scan_progress_payload_shape(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Every progress payload has the four keys the UI hook reads."""
    library_root = tmp_path / "library"
    library_root.mkdir()
    _seed_files(library_root, count=3)
    library = Library(
        name="movies", root_path=str(library_root / "lib"), kind="movies"
    )
    await LibraryRepository(session).add(library)
    await session.commit()

    bus = EventBus()
    bus.clear()
    events: list[dict[str, object]] = []
    bus.subscribe(
        "scan.progress", lambda e: events.append(dict(getattr(e, "payload", {}))),
    )
    scanner = Scanner(
        session=session,
        event_bus=bus,
        ffprobe=StubFfprobe(),  # type: ignore[arg-type]
    )
    await scanner.scan(library, options=ScanOptions(mode="full"))

    for e in events:
        assert set(e.keys()) == {
            "run_id",
            "library_id",
            "files_seen",
            "files_total_estimate",
        }


@pytest.mark.asyncio
async def test_scanner_reuses_pre_existing_run_row(
    session: AsyncSession, tmp_path: Path
) -> None:
    """When the API enqueues a scan it pre-creates a ``queued`` ScanRun
    and passes the row to the worker. Scanner.scan() must mutate that
    row instead of creating a second one, otherwise the pre-created
    queued row stays at ``queued`` forever and the single-flight
    check 409s subsequent triggers.
    """
    from sqlalchemy import select

    library_root = tmp_path / "library"
    library_root.mkdir()
    _seed_files(library_root, count=3)
    library = Library(
        name="movies", root_path=str(library_root / "lib"), kind="movies"
    )
    await LibraryRepository(session).add(library)
    await session.commit()

    pre = ScanRun(
        library_id=library.id,
        mode="full",
        status="queued",
        options={"follow_symlinks": False},
    )
    session.add(pre)
    await session.commit()
    pre_id = pre.id

    scanner = Scanner(
        session=session,
        event_bus=EventBus(),
        ffprobe=StubFfprobe(),  # type: ignore[arg-type]
    )
    report = await scanner.scan(
        library, options=ScanOptions(mode="full"), run=pre
    )

    rows = (
        await session.execute(
            select(ScanRun).where(ScanRun.library_id == library.id)
        )
    ).scalars().all()
    assert len(rows) == 1, "scanner should reuse the pre-existing run row"
    assert rows[0].id == pre_id
    assert rows[0].status in {"completed", "failed"}
    assert rows[0].started_at is not None
    assert report.run_id == pre_id


# ── /scans/all + async default — live API tests ─────────────────
@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "stage8_api.db"
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


async def _seed_libraries(tmp_path: Path) -> list[str]:
    """Seed three libraries: two enabled, one disabled. Returns the
    enabled ids only."""
    enabled_ids: list[str] = []
    async with get_database().session() as sess:
        for i, name in enumerate(["A", "B"]):
            root = tmp_path / f"lib_{name}"
            root.mkdir()
            lib = Library(
                id=f"lib-{name}", name=name, root_path=str(root),
                kind="movies", enabled=True,
            )
            sess.add(lib)
            enabled_ids.append(lib.id)
        disabled_root = tmp_path / "lib_C"
        disabled_root.mkdir()
        sess.add(
            Library(
                id="lib-C", name="C", root_path=str(disabled_root),
                kind="movies", enabled=False,
            )
        )
        await sess.commit()
    return enabled_ids


@pytest.mark.asyncio
async def test_scan_all_returns_one_run_per_enabled_library(
    client: AsyncClient, tmp_path: Path
) -> None:
    """``POST /scans/all`` enqueues exactly the enabled libraries."""
    headers = await _admin_headers(client)
    enabled = await _seed_libraries(tmp_path)

    r = await client.post(
        "/api/v1/scans/all",
        headers=headers,
        json={"mode": "full", "follow_symlinks": False},
    )
    assert r.status_code == 202, r.text
    runs = r.json()
    # Two enabled libraries → two runs. The disabled one is excluded.
    assert len(runs) == 2
    library_ids = {r["library_id"] for r in runs}
    assert library_ids == set(enabled)
    # Each run is in either "queued" (queue accepted the job) or
    # "failed" (queue unavailable) state — both are valid post-202
    # outcomes per the endpoint's docstring.
    for run in runs:
        assert run["status"] in ("queued", "failed")


@pytest.mark.asyncio
async def test_scan_all_skips_libraries_with_active_scan(
    client: AsyncClient, tmp_path: Path
) -> None:
    """If one library already has a running scan, ``/scans/all``
    silently skips it (no 409 — the rest of the batch must still
    run)."""
    headers = await _admin_headers(client)
    enabled = await _seed_libraries(tmp_path)

    # Park a running ScanRun on lib-A.
    async with get_database().session() as sess:
        sess.add(
            ScanRun(
                library_id="lib-A",
                mode="full",
                status="running",
                started_at=utcnow(),
                options={},
            )
        )
        await sess.commit()

    r = await client.post(
        "/api/v1/scans/all",
        headers=headers,
        json={"mode": "full", "follow_symlinks": False},
    )
    assert r.status_code == 202, r.text
    runs = r.json()
    # Only lib-B should be queued; lib-A's running scan blocks it.
    assert len(runs) == 1
    assert runs[0]["library_id"] == "lib-B"
    assert enabled  # silence unused-var lint


@pytest.mark.asyncio
async def test_scan_all_requires_admin(client: AsyncClient, tmp_path: Path) -> None:
    """Non-admin gets 401/403."""
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
        "/api/v1/scans/all",
        headers=headers,
        json={"mode": "full", "follow_symlinks": False},
    )
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_scan_all_empty_when_no_enabled_libraries(
    client: AsyncClient,
) -> None:
    """Fresh install with no libraries → empty array (not 404, not 500)."""
    headers = await _admin_headers(client)
    r = await client.post(
        "/api/v1/scans/all",
        headers=headers,
        json={"mode": "full", "follow_symlinks": False},
    )
    assert r.status_code == 202
    assert r.json() == []


@pytest.mark.asyncio
async def test_per_library_default_is_async(
    client: AsyncClient, tmp_path: Path
) -> None:
    """The per-library endpoint defaults to ``enqueue=true``.

    Concretely: posting WITHOUT a query string returns a row whose
    status is ``queued`` (or ``failed`` if the queue is unreachable)
    rather than ``completed``. Pre-Stage-8 this returned a completed
    sync run, which is what killed long scans by hitting the gunicorn
    timeout.
    """
    headers = await _admin_headers(client)
    await _seed_libraries(tmp_path)
    r = await client.post(
        "/api/v1/scans/libraries/lib-A",
        headers=headers,
        json={"mode": "full", "follow_symlinks": False},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    # Async path → "queued" or "failed". NEVER "completed".
    assert body["status"] in ("queued", "failed")
    assert body["status"] != "completed"


@pytest.mark.asyncio
async def test_per_library_explicit_enqueue_false_still_runs_sync(
    client: AsyncClient, tmp_path: Path
) -> None:
    """The ``?enqueue=false`` escape hatch must still work for tests
    and small-library operators who want the legacy behaviour."""
    headers = await _admin_headers(client)
    await _seed_libraries(tmp_path)
    r = await client.post(
        "/api/v1/scans/libraries/lib-A?enqueue=false",
        headers=headers,
        json={"mode": "full", "follow_symlinks": False},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    # Sync path → "completed" (no files in the lib, so it's an empty
    # but successful scan).
    assert body["status"] == "completed"
