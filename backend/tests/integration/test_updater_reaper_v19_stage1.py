"""Updater reaper + force-clear contract (v1.9 Stage 1.2).

Pins:

  1. ``UpdateApplyRepository.has_open(timeout_seconds=N)`` reaps any
     ``requested``/``running`` row whose ``started_at`` is older than
     ``N`` seconds, transitioning it to ``failed`` with a sentinel
     error message.
  2. After the reaper runs, ``request_apply`` for a fresh version no
     longer raises ``ConflictError`` — the wedged row no longer
     counts as open.
  3. ``POST /api/v1/updater/applies/{id}/force-clear`` (admin) flips
     a stuck row to ``failed`` and returns the updated row.
  4. Force-clear is admin-only and 404s on unknown ids and 422s on
     rows that are already terminal.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.update_apply import UpdateApply
from app.models.user import User
from app.services.repositories.updater import UpdateApplyRepository
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database
from app.utils.datetime import utcnow

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "updater_reaper_v19.db"
    sentinel_path = tmp_path / "updater" / "apply.request"
    status_path = tmp_path / "updater" / "apply.status"

    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    monkeypatch.setenv("AUDITARR_APP_VERSION", "1.0.0")
    monkeypatch.setenv("AUDITARR_UPDATE_FEED_URL", "https://example.test/feed")
    monkeypatch.setenv("AUDITARR_UPDATE_APPLY_SENTINEL", str(sentinel_path))
    monkeypatch.setenv(
        "AUDITARR_UPDATE_APPLY_STATUS_PATH", str(status_path)
    )
    monkeypatch.setenv("AUDITARR_UPDATE_INSTALL_MODE", "docker")
    # Short timeout so tests don't have to backdate by 30 minutes.
    monkeypatch.setenv("AUDITARR_UPDATE_APPLY_TIMEOUT_SECONDS", "60")

    from app.core.settings import get_settings
    from app.updater.install_mode import reset_cache_for_tests

    get_settings.cache_clear()
    reset_cache_for_tests()

    # Default feed response: a fresh version is available. Tests that
    # care about the feed body override this; the reaper tests use the
    # default.
    captured: dict = {"body": {"tag_name": "v1.4.0", "body": "Bug fixes."}}
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        if "transport" not in kwargs:

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json=captured["body"])

            kwargs["transport"] = httpx.MockTransport(handler)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

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


async def _insert_stale_apply(
    *,
    minutes_old: int = 60,
    status: str = "running",
    to_version: str = "1.2.0",
) -> str:
    """Park a stale ``UpdateApply`` row in the DB.

    Returns the row id so callers can target it.
    """
    started_at = utcnow() - _dt.timedelta(minutes=minutes_old)
    async with get_database().session() as sess:
        row = UpdateApply(
            status=status,
            from_version="1.0.0",
            to_version=to_version,
            started_at=started_at,
        )
        sess.add(row)
        await sess.commit()
        return row.id


# ── Repository-level reaper ─────────────────────────────────────
@pytest.mark.asyncio
async def test_reap_stale_transitions_old_rows_to_failed(
    client: AsyncClient,
) -> None:
    """A ``requested`` row older than ``timeout_seconds`` is force-
    marked ``failed`` by ``has_open(timeout_seconds=…)``."""
    row_id = await _insert_stale_apply(minutes_old=30, status="requested")

    async with get_database().session() as sess:
        repo = UpdateApplyRepository(sess)
        # 60-second timeout — the row is 30 minutes old.
        result = await repo.has_open(timeout_seconds=60)
        await sess.commit()

    # has_open returns False because the row was reaped first.
    assert result is False

    async with get_database().session() as sess:
        row = await UpdateApplyRepository(sess).get(row_id)
        assert row is not None
        assert row.status == "failed"
        assert row.finished_at is not None
        assert row.error is not None
        assert "reaper" in row.error


@pytest.mark.asyncio
async def test_reap_stale_leaves_fresh_rows_open(client: AsyncClient) -> None:
    """A row that's only seconds old should NOT be reaped, even if
    a timeout is supplied."""
    row_id = await _insert_stale_apply(minutes_old=0, status="running")

    async with get_database().session() as sess:
        repo = UpdateApplyRepository(sess)
        result = await repo.has_open(timeout_seconds=3600)
        await sess.commit()

    assert result is True  # the fresh row is still open

    async with get_database().session() as sess:
        row = await UpdateApplyRepository(sess).get(row_id)
        assert row is not None
        assert row.status == "running"
        assert row.error is None


@pytest.mark.asyncio
async def test_has_open_without_timeout_skips_reap(client: AsyncClient) -> None:
    """Backwards-compat: callers that omit ``timeout_seconds`` get the
    pre-1.9 behaviour — no reaping, every open row counts."""
    row_id = await _insert_stale_apply(minutes_old=120, status="running")

    async with get_database().session() as sess:
        repo = UpdateApplyRepository(sess)
        result = await repo.has_open()  # no timeout
        await sess.commit()

    assert result is True

    async with get_database().session() as sess:
        row = await UpdateApplyRepository(sess).get(row_id)
        assert row is not None
        assert row.status == "running"  # not reaped


# ── Service / API integration ───────────────────────────────────
@pytest.mark.asyncio
async def test_request_apply_succeeds_after_reaping_stale_row(
    client: AsyncClient,
) -> None:
    """The headline regression the reaper is built to fix.

    Pre-1.9 a wedged ``running`` row would block ``request_apply``
    forever with a 409. With the reaper running inside ``has_open``,
    requesting a fresh apply on a fresh version succeeds: the stale
    row gets transitioned to ``failed`` first, then the new request
    creates a brand-new ``requested`` row.
    """
    headers = await _admin_headers(client)
    stale_id = await _insert_stale_apply(minutes_old=120, status="running")

    response = await client.post(
        "/api/v1/updater/apply",
        headers=headers,
        json={"to_version": "1.5.0"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "requested"
    assert body["to_version"] == "1.5.0"
    assert body["id"] != stale_id  # genuinely a new row

    # The stale row was reaped.
    async with get_database().session() as sess:
        stale = await UpdateApplyRepository(sess).get(stale_id)
        assert stale is not None
        assert stale.status == "failed"
        assert stale.error and "reaper" in stale.error


@pytest.mark.asyncio
async def test_force_clear_endpoint_transitions_open_row_to_failed(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    # Fresh row — not eligible for time-based reaping. Force-clear
    # still works.
    row_id = await _insert_stale_apply(minutes_old=0, status="requested")

    response = await client.post(
        f"/api/v1/updater/applies/{row_id}/force-clear",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "failed"
    assert body["id"] == row_id
    assert body["error"] is not None
    assert "force-clear" in body["error"]


@pytest.mark.asyncio
async def test_force_clear_404s_for_unknown_id(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/updater/applies/no-such-id/force-clear",
        headers=headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_force_clear_422s_for_already_terminal_row(
    client: AsyncClient,
) -> None:
    """Force-clear on a completed row is a user error, not a no-op."""
    headers = await _admin_headers(client)
    async with get_database().session() as sess:
        row = UpdateApply(
            status="completed",
            from_version="1.0.0",
            to_version="1.1.0",
            started_at=utcnow(),
            finished_at=utcnow(),
        )
        sess.add(row)
        await sess.commit()
        row_id = row.id

    response = await client.post(
        f"/api/v1/updater/applies/{row_id}/force-clear",
        headers=headers,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_force_clear_requires_admin(client: AsyncClient) -> None:
    """A non-admin user can't force-clear."""
    # Register a non-admin user.
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

    row_id = await _insert_stale_apply(minutes_old=0, status="requested")
    response = await client.post(
        f"/api/v1/updater/applies/{row_id}/force-clear",
        headers=headers,
    )
    assert response.status_code in (401, 403)
