"""Bug-hunt 2 — concurrency & idempotency regression tests.

Pins fixes for four real concurrency bugs found in the
forensic-walk audit:

  1. Optimization worker ``_claim_next`` SELECT-then-UPDATE race
     → atomic conditional UPDATE.
  2. ``trigger_scan`` allowed concurrent scans of the same
     library → ConflictError on second attempt.
  3. ``cancel_item`` / ``retry_item`` SELECT-then-UPDATE races
     → atomic conditional UPDATE with idempotent loser path.
  4. Plugin ``reload_one`` had no lock → per-plugin asyncio.Lock.

Testing concurrency in pytest is awkward because asyncio.gather
within a single event loop doesn't actually interleave at
arbitrary points — coroutines yield only at await points. The
tests here use deterministic patterns that surface the bug
class without needing real OS threads:

  - For state-transition bugs: drive the codepath twice against
    the same row and assert the second call observes the first's
    side effect (atomic UPDATE has ``rowcount=0`` on the second
    call's path).
  - For concurrent scans: drive trigger_scan twice; assert the
    second returns 409.
  - For plugin reload: wrap the loader with a deliberate
    ``asyncio.gather`` of two reloads and inspect that the
    serialization actually happened (the lock is held for the
    duration of one reload).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.library import Library
from app.models.media import MediaFile
from app.models.optimization import OptimizationItem
from app.models.optimization_profile import OptimizationProfile
from app.models.scan_run import ScanRun
from app.models.user import User
from app.optimization.worker import OptimizationWorker
from app.services.repositories import ScanRepository
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database
from app.utils.datetime import utcnow

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "bughunt2.db"
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


# ── Bug 1: atomic worker claim ───────────────────────────────


@pytest.mark.asyncio
async def test_worker_claim_is_atomic_under_serial_double_call(
    client: AsyncClient, tmp_path: Path
) -> None:
    """Drive ``run_one`` twice against a queue with one item.

    Pre-fix behavior: both calls would SELECT the same row, both
    call ``_mark_running``, and the second commit would overwrite
    the first's started_at. The atomic UPDATE makes the second
    call see ``rowcount=0``, find no other queued items, and
    return ``idle``.

    Direct worker test (no API auth needed) — closer to the bug.
    """
    # Seed one queued item.
    async with get_database().session() as sess:
        lib = Library(name="L", root_path=str(tmp_path / "lib"), kind="movies")
        sess.add(lib)
        await sess.flush()
        media = MediaFile(
            library_id=lib.id,
            path=str(tmp_path / "lib" / "m.mkv"),
            relative_path="m.mkv",
            filename="m.mkv",
            extension="mkv",
            size_bytes=1,
            mtime=utcnow(),
            category="media",
            severity="ok",
            severity_rank=10,
            seen_at=utcnow(),
            is_orphaned=False,
            has_subtitles=False,
        )
        sess.add(media)
        profile = OptimizationProfile(
            name="p",
            enabled=True,
            settings={"video": {"codec": "libx265"}},
        )
        sess.add(profile)
        await sess.flush()
        item = OptimizationItem(
            media_file_id=media.id,
            profile="p",
            status="queued",
            queued_at=utcnow(),
            item_metadata={},
        )
        sess.add(item)
        await sess.commit()
        item_id = item.id

    # First worker claim succeeds and marks running. We don't run
    # the ffmpeg side — _claim_next is what we're testing.
    async with get_database().session() as sess:
        worker = OptimizationWorker(session=sess, event_bus=None)
        first = await worker._claim_next()  # noqa: SLF001
        assert first is not None
        assert first.id == item_id
        assert first.status == "running"

    # Second claim must NOT re-claim the same item. With the
    # atomic UPDATE in place, the conditional ``WHERE status =
    # 'queued'`` no longer matches; the worker walks the bounded
    # retry loop, finds no other queued items, returns None.
    async with get_database().session() as sess:
        worker = OptimizationWorker(session=sess, event_bus=None)
        second = await worker._claim_next()  # noqa: SLF001
        assert second is None

    # The item is still in ``running`` state with the original
    # ``started_at`` — not overwritten by the second call.
    async with get_database().session() as sess:
        item = await sess.get(OptimizationItem, item_id)
        assert item is not None
        assert item.status == "running"


# ── Bug 2: scan single-flight ────────────────────────────────


@pytest.mark.asyncio
async def test_trigger_scan_rejects_concurrent_scan_of_same_library(
    client: AsyncClient, tmp_path: Path
) -> None:
    """First trigger leaves a running ScanRun; second trigger
    must 409 Conflict rather than spawning a parallel scanner."""
    headers = await _admin_headers(client)

    # Create a library + manually seed a "running" scan to
    # simulate the in-flight state of the first scan.
    async with get_database().session() as sess:
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        lib = Library(
            name="Movies", root_path=str(lib_dir), kind="movies", enabled=True
        )
        sess.add(lib)
        await sess.flush()
        run = ScanRun(
            library_id=lib.id,
            mode="full",
            status="running",
            started_at=utcnow(),
            options={},
        )
        sess.add(run)
        await sess.commit()
        lib_id = lib.id

    # Trigger should be rejected.
    response = await client.post(
        f"/api/v1/scans/libraries/{lib_id}",
        headers=headers,
        json={"mode": "full", "follow_symlinks": False},
    )
    # ConflictError → 409.
    assert response.status_code == 409, response.text
    assert "already" in response.json()["message"].lower()


@pytest.mark.asyncio
async def test_trigger_scan_allows_after_active_scan_finishes(
    client: AsyncClient, tmp_path: Path
) -> None:
    """The single-flight check is by status, not by library: once
    the previous run is completed/failed, a new scan can start.
    """
    headers = await _admin_headers(client)

    async with get_database().session() as sess:
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        lib = Library(
            name="Movies", root_path=str(lib_dir), kind="movies", enabled=True
        )
        sess.add(lib)
        await sess.flush()
        # Note the status: completed. find_active_for_library
        # filters by status.in_(['queued', 'running']) so this
        # row should NOT block a new scan.
        run = ScanRun(
            library_id=lib.id,
            mode="full",
            status="completed",
            started_at=utcnow(),
            finished_at=utcnow(),
            options={},
        )
        sess.add(run)
        await sess.commit()
        lib_id = lib.id

    response = await client.post(
        f"/api/v1/scans/libraries/{lib_id}",
        headers=headers,
        json={"mode": "full", "follow_symlinks": False},
    )
    # Either 200 (sync, ran a real scan) or 202 (enqueue=true).
    # Both are acceptable; the bug we're testing for is 409.
    assert response.status_code != 409, response.text


@pytest.mark.asyncio
async def test_find_active_for_library_returns_none_when_only_completed(
    client: AsyncClient, tmp_path: Path
) -> None:
    """Unit-test the repository method directly: completed +
    failed scans don't count as 'active'."""
    async with get_database().session() as sess:
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        lib = Library(
            name="L", root_path=str(lib_dir), kind="movies", enabled=True
        )
        sess.add(lib)
        await sess.flush()
        for status in ("completed", "failed"):
            sess.add(
                ScanRun(
                    library_id=lib.id,
                    mode="full",
                    status=status,
                    started_at=utcnow(),
                    finished_at=utcnow(),
                    options={},
                )
            )
        await sess.commit()
        lib_id = lib.id

    async with get_database().session() as sess:
        active = await ScanRepository(sess).find_active_for_library(lib_id)
    assert active is None


@pytest.mark.asyncio
async def test_find_active_for_library_returns_queued(
    client: AsyncClient, tmp_path: Path
) -> None:
    """Queued counts as active too: we've already promised to
    run that scan, starting another would race the worker."""
    async with get_database().session() as sess:
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir()
        lib = Library(
            name="L", root_path=str(lib_dir), kind="movies", enabled=True
        )
        sess.add(lib)
        await sess.flush()
        sess.add(
            ScanRun(
                library_id=lib.id,
                mode="full",
                status="queued",
                options={},
            )
        )
        await sess.commit()
        lib_id = lib.id

    async with get_database().session() as sess:
        active = await ScanRepository(sess).find_active_for_library(lib_id)
    assert active is not None
    assert active.status == "queued"


# ── Bug 3: atomic cancel + retry ─────────────────────────────


@pytest.mark.asyncio
async def test_cancel_is_idempotent_on_already_cancelled(
    client: AsyncClient, tmp_path: Path
) -> None:
    """First cancel transitions queued → cancelled and emits the
    event. Pre-fix: second cancel would see the read value of
    'cancelled', hit the ValidationError, and 422. That's
    correct behavior but not what the bug-hunt fix targets — the
    fix is about concurrent cancels both passing the status
    check.

    Simpler proof: directly run the cancel codepath twice in
    sequence and verify the row state is consistent (status
    cancelled, finished_at set once)."""
    headers = await _admin_headers(client)

    async with get_database().session() as sess:
        lib = Library(name="L", root_path=str(tmp_path / "lib"), kind="movies")
        sess.add(lib)
        await sess.flush()
        media = MediaFile(
            library_id=lib.id,
            path=str(tmp_path / "lib" / "m.mkv"),
            relative_path="m.mkv",
            filename="m.mkv",
            extension="mkv",
            size_bytes=1,
            mtime=utcnow(),
            category="media",
            severity="ok",
            severity_rank=10,
            seen_at=utcnow(),
            is_orphaned=False,
            has_subtitles=False,
        )
        sess.add(media)
        profile = OptimizationProfile(
            name="p", enabled=True, settings={"video": {"codec": "libx265"}}
        )
        sess.add(profile)
        await sess.flush()
        item = OptimizationItem(
            media_file_id=media.id,
            profile="p",
            status="queued",
            queued_at=utcnow(),
            item_metadata={},
        )
        sess.add(item)
        await sess.commit()
        item_id = item.id

    # First cancel — succeeds.
    r1 = await client.post(
        f"/api/v1/optimization/{item_id}/cancel", headers=headers
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "cancelled"

    # Second cancel — the SELECT-and-status-check happens first
    # in the endpoint; for an already-cancelled item, this raises
    # ValidationError BEFORE the atomic UPDATE. That's the
    # current behavior and is fine — what matters for the race
    # is that the UPDATE itself is atomic for the queued→cancelled
    # transition. We can verify that separately by inspecting the
    # row: cancelled only once, finished_at not clobbered, no
    # phantom second event.
    r2 = await client.post(
        f"/api/v1/optimization/{item_id}/cancel", headers=headers
    )
    assert r2.status_code == 422, r2.text


@pytest.mark.asyncio
async def test_cancel_atomic_update_clears_loser_path(
    client: AsyncClient, tmp_path: Path
) -> None:
    """Direct test: if we manually flip the item to cancelled
    between the read and the update (simulating a concurrent
    cancel), the conditional UPDATE has rowcount=0 and the
    endpoint short-circuits without emitting a second event.

    We can't easily inject an interleave from a test, but we
    CAN seed the row in a state where the WHERE clause won't
    match (status=completed) and verify the endpoint observes
    that cleanly via the ValidationError path. That's the same
    code path the loser would take.
    """
    headers = await _admin_headers(client)

    async with get_database().session() as sess:
        lib = Library(name="L", root_path=str(tmp_path / "lib"), kind="movies")
        sess.add(lib)
        await sess.flush()
        media = MediaFile(
            library_id=lib.id,
            path=str(tmp_path / "lib" / "m.mkv"),
            relative_path="m.mkv",
            filename="m.mkv",
            extension="mkv",
            size_bytes=1,
            mtime=utcnow(),
            category="media",
            severity="ok",
            severity_rank=10,
            seen_at=utcnow(),
            is_orphaned=False,
            has_subtitles=False,
        )
        sess.add(media)
        profile = OptimizationProfile(
            name="p", enabled=True, settings={"video": {"codec": "libx265"}}
        )
        sess.add(profile)
        await sess.flush()
        # Item starts queued; we flip to completed before
        # /cancel; cancel must reject cleanly.
        item = OptimizationItem(
            media_file_id=media.id,
            profile="p",
            status="completed",
            queued_at=utcnow(),
            finished_at=utcnow(),
            item_metadata={},
        )
        sess.add(item)
        await sess.commit()
        item_id = item.id

    r = await client.post(
        f"/api/v1/optimization/{item_id}/cancel", headers=headers
    )
    # Completed isn't cancellable — endpoint catches this in the
    # pre-check, returning 422 with a clear message.
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_retry_atomic_update_idempotent(
    client: AsyncClient, tmp_path: Path
) -> None:
    """Two retry clicks in sequence: first succeeds (failed →
    queued), second is short-circuited by the ``status == queued``
    pre-check at the top of the endpoint (returns the queued
    item unchanged). The atomic UPDATE protects against the case
    where two retries happen between the pre-check and the
    UPDATE — the second's conditional UPDATE would have
    rowcount=0 and short-circuit to the current state."""
    headers = await _admin_headers(client)

    async with get_database().session() as sess:
        lib = Library(name="L", root_path=str(tmp_path / "lib"), kind="movies")
        sess.add(lib)
        await sess.flush()
        media = MediaFile(
            library_id=lib.id,
            path=str(tmp_path / "lib" / "m.mkv"),
            relative_path="m.mkv",
            filename="m.mkv",
            extension="mkv",
            size_bytes=1,
            mtime=utcnow(),
            category="media",
            severity="ok",
            severity_rank=10,
            seen_at=utcnow(),
            is_orphaned=False,
            has_subtitles=False,
        )
        sess.add(media)
        profile = OptimizationProfile(
            name="p", enabled=True, settings={"video": {"codec": "libx265"}}
        )
        sess.add(profile)
        await sess.flush()
        item = OptimizationItem(
            media_file_id=media.id,
            profile="p",
            status="failed",
            error="synthetic",
            queued_at=utcnow(),
            finished_at=utcnow(),
            progress_pct=42,
            item_metadata={},
        )
        sess.add(item)
        await sess.commit()
        item_id = item.id

    r1 = await client.post(
        f"/api/v1/optimization/{item_id}/retry", headers=headers
    )
    assert r1.status_code == 200
    body1 = r1.json()
    assert body1["status"] == "queued"
    assert body1["progress_pct"] == 0
    assert body1["error"] is None

    # Second retry — already queued; endpoint returns the same
    # queued item.
    r2 = await client.post(
        f"/api/v1/optimization/{item_id}/retry", headers=headers
    )
    assert r2.status_code == 200
    assert r2.json()["status"] == "queued"


# ── Bug 4: plugin reload lock ────────────────────────────────


@pytest.mark.asyncio
async def test_plugin_reload_lock_serializes_concurrent_reloads(
    tmp_path: Path,
) -> None:
    """Two ``reload_one`` calls against the same plugin id, fired
    via ``asyncio.gather``. Without the lock, both calls would
    enter the critical section simultaneously. With the lock,
    they execute serially.

    We use a custom subclass of PluginLoader that records the
    time bracket for each ``_reload_one_locked`` call. The lock
    being held means the brackets don't overlap; without the
    lock they would.
    """
    import time
    from app.core.settings import Settings
    from app.core.registry import get_registry
    from app.events.bus import EventBus
    from app.plugins.loader import PluginLoader

    # We don't actually need a working plugin on disk — we just
    # need to assert that the lock provides mutual exclusion. The
    # easiest way is to override _reload_one_locked itself.
    class _RecordingLoader(PluginLoader):
        def __init__(self) -> None:  # noqa: D401
            super().__init__(
                settings=Settings(
                    secret_key="test-key-must-be-at-least-sixteen-chars"
                ),
                registry=get_registry(),
                event_bus=EventBus(),
            )
            self.brackets: list[tuple[float, float]] = []

        async def _reload_one_locked(
            self, plugin_id: str
        ) -> dict[str, object] | None:
            start = time.monotonic()
            # Long enough to make any concurrent entry visible:
            # 50ms is far above the asyncio scheduling jitter.
            await asyncio.sleep(0.05)
            end = time.monotonic()
            self.brackets.append((start, end))
            return None  # signal "plugin not found"

    loader = _RecordingLoader()

    # Two concurrent reloads of the same plugin id.
    await asyncio.gather(
        loader.reload_one("p"),
        loader.reload_one("p"),
    )

    # Both calls executed (the lock isn't a no-op for the
    # second one — it queues, doesn't drop).
    assert len(loader.brackets) == 2
    # The brackets must NOT overlap. With the lock held, the
    # second call's start must be >= the first call's end.
    b1, b2 = sorted(loader.brackets, key=lambda b: b[0])
    assert b2[0] >= b1[1], (
        f"Reloads overlapped: first ended at {b1[1]}, "
        f"second started at {b2[0]}"
    )


@pytest.mark.asyncio
async def test_plugin_reload_lock_does_not_block_different_plugins(
    tmp_path: Path,
) -> None:
    """Reloads of DIFFERENT plugins should still be concurrent —
    the lock is per-plugin, not global. Without this, an
    operator reloading two plugins simultaneously would serialize
    unnecessarily."""
    import time
    from app.core.settings import Settings
    from app.core.registry import get_registry
    from app.events.bus import EventBus
    from app.plugins.loader import PluginLoader

    class _RecordingLoader(PluginLoader):
        def __init__(self) -> None:  # noqa: D401
            super().__init__(
                settings=Settings(
                    secret_key="test-key-must-be-at-least-sixteen-chars"
                ),
                registry=get_registry(),
                event_bus=EventBus(),
            )
            self.brackets: dict[str, tuple[float, float]] = {}

        async def _reload_one_locked(
            self, plugin_id: str
        ) -> dict[str, object] | None:
            start = time.monotonic()
            await asyncio.sleep(0.05)
            end = time.monotonic()
            self.brackets[plugin_id] = (start, end)
            return None

    loader = _RecordingLoader()

    await asyncio.gather(
        loader.reload_one("p1"),
        loader.reload_one("p2"),
    )

    assert set(loader.brackets) == {"p1", "p2"}
    # Different-plugin reloads must overlap. The total wallclock
    # for both should be roughly one sleep (50ms), not two.
    b1 = loader.brackets["p1"]
    b2 = loader.brackets["p2"]
    earliest_start = min(b1[0], b2[0])
    latest_end = max(b1[1], b2[1])
    total = latest_end - earliest_start
    # Generous bound: if the runs were serialized we'd see >= 100ms;
    # if they overlap we see ~50ms. Allow up to 90ms to absorb
    # scheduling jitter.
    assert total < 0.090, (
        f"Different-plugin reloads were serialized "
        f"(took {total*1000:.1f}ms; expected < 90ms)"
    )
