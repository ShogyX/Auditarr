"""Stage 10 (v1.7) — VirusTotal status endpoint tests.

Plan §525:
    Call the status endpoint; assert the shape.

We exercise the endpoint end-to-end through the real ASGI app:
  1. Bootstrap admin user + login → bearer token.
  2. Hit ``GET /api/v1/integrations/virustotal/status``.
  3. Assert the response shape matches the plan §516 contract
     PLUS the addendum-B.7 three-window split.

The endpoint surfaces operator-visible state about the VT
integration: how close each quota window is to its cap, the
queue size, the last lookup timestamp, and configuration state.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update

from app.main import create_app
from app.models.integration import Integration
from app.models.library import Library
from app.models.media import MediaFile
from app.models.user import User
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[dict]:
    db_path = tmp_path / "vt_status.db"
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
    client = AsyncClient(transport=transport, base_url="http://test")

    reg = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "admin10@example.com",
            "username": "admin10",
            "password": PASSWORD,
        },
    )
    assert reg.status_code in (200, 201), reg.text
    user = reg.json()
    async with db.session() as sess:
        await sess.execute(
            update(User).where(User.id == user["id"]).values(role="admin")
        )
        await sess.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "admin10", "password": PASSWORD},
    )
    assert login.status_code == 200, login.text
    headers = {"authorization": f"Bearer {login.json()['access_token']}"}

    try:
        yield {"client": client, "db": db, "headers": headers}
    finally:
        await client.aclose()
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        try:
            await get_redis().disconnect()
        except Exception:  # noqa: BLE001
            pass
        get_settings.cache_clear()


# ── Test 1 — Plan §525: shape check on empty state ─────────────


@pytest.mark.asyncio
async def test_status_endpoint_shape_when_no_integration(env) -> None:
    """When no VT integration is configured the endpoint still
    returns a sensible empty-state response (zero counters,
    ``configured=False``) so the frontend renders "Not
    configured" rather than 404'ing."""
    client = env["client"]
    response = await client.get(
        "/api/v1/integrations/virustotal/status", headers=env["headers"]
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # Three-window quota fields (addendum B.7).
    for prefix in ("minute", "day", "month"):
        assert f"{prefix}_used" in body
        assert f"{prefix}_cap" in body
        assert f"{prefix}_remaining" in body
        assert body[f"{prefix}_used"] == 0
        assert body[f"{prefix}_cap"] > 0
        assert body[f"{prefix}_remaining"] == body[f"{prefix}_cap"]

    # Plan §516 legacy aliases.
    assert body["quota_used_today"] == 0
    assert body["quota_limit"] == body["day_cap"]

    # Queue + last_check_at.
    assert body["queue_size"] == 0
    assert body["last_check_at"] is None

    # Empty-state flags.
    assert body["configured"] is False
    assert body["enabled"] is False


# ── Test 2 — Status when VT integration is configured ─────────


@pytest.mark.asyncio
async def test_status_endpoint_shape_when_integration_configured(env) -> None:
    """With a configured VT integration the endpoint reflects
    its config (custom daily_quota / monthly_quota) and
    surfaces enabled + configured flags."""
    db = env["db"]
    async with db.session() as session:
        session.add(
            Integration(
                name="VirusTotal",
                kind="virustotal",
                enabled=True,
                poll_interval_seconds=900,
                config={
                    "daily_quota": 100,
                    "monthly_quota": 1000,
                    "timeout_seconds": 10,
                },
                health_status="ok",
            )
        )
        await session.commit()

    client = env["client"]
    response = await client.get(
        "/api/v1/integrations/virustotal/status", headers=env["headers"]
    )
    assert response.status_code == 200
    body = response.json()

    # The custom config flows through.
    assert body["day_cap"] == 100
    assert body["month_cap"] == 1000
    assert body["configured"] is True
    assert body["enabled"] is True
    # And the legacy alias.
    assert body["quota_limit"] == 100


# ── Test 3 — Queue size reflects vt_queue COUNT(*) ─────────────


@pytest.mark.asyncio
async def test_status_endpoint_reports_queue_size(env) -> None:
    """The status endpoint's ``queue_size`` field reads
    ``COUNT(*) FROM vt_queue``. Insert three rows and assert
    the endpoint reports 3."""
    db = env["db"]
    async with db.session() as session:
        lib = Library(
            name="Movies", root_path="/mnt/media/Movies", kind="movies"
        )
        session.add(lib)
        await session.flush()

        import datetime as _dt

        from app.models.vt_queue import VtQueueItem

        media_ids: list[str] = []
        for i in range(3):
            mf = MediaFile(
                library_id=lib.id,
                path=f"/mnt/media/Movies/q-{i}.mkv",
                relative_path=f"q-{i}.mkv",
                filename=f"q-{i}.mkv",
                extension="mkv",
                size_bytes=1024 * 1024,
                mtime=_dt.datetime.now(_dt.UTC),
                category="media",
                severity="ok",
                severity_rank=10,
                has_subtitles=False,
                seen_at=_dt.datetime.now(_dt.UTC),
                is_orphaned=False,
            )
            session.add(mf)
            await session.flush()
            media_ids.append(mf.id)
            session.add(
                VtQueueItem(
                    media_file_id=mf.id,
                    enqueued_at=_dt.datetime.now(_dt.UTC),
                    attempt_count=0,
                )
            )
        await session.commit()

    client = env["client"]
    response = await client.get(
        "/api/v1/integrations/virustotal/status", headers=env["headers"]
    )
    assert response.status_code == 200
    assert response.json()["queue_size"] == 3


# ── Test 4 — Auth gating: anonymous request rejected ───────────


@pytest.mark.asyncio
async def test_status_endpoint_requires_auth(env) -> None:
    """The endpoint is gated behind CurrentUser dependency —
    an anonymous request returns 401."""
    client = env["client"]
    response = await client.get("/api/v1/integrations/virustotal/status")
    assert response.status_code in (401, 403)


# ── Test 5 — Last-check timestamp surfaces after a lookup ──────


@pytest.mark.asyncio
async def test_status_endpoint_surfaces_last_check_at_after_lookup(env) -> None:
    """After a successful quota check the singleton's
    ``last_check_at`` is set; the next status call surfaces
    it. This pins the wiring between the plugin's quota state
    and the endpoint's read path."""
    from plugins.virustotal.backend import _check_and_increment_quota

    allowed, _ = await _check_and_increment_quota(
        minute_cap=4, daily_cap=500, monthly_cap=15500, event_bus=None
    )
    assert allowed is True

    client = env["client"]
    response = await client.get(
        "/api/v1/integrations/virustotal/status", headers=env["headers"]
    )
    body = response.json()
    assert body["last_check_at"] is not None
    # Counters in all three windows bumped by 1.
    assert body["minute_used"] == 1
    assert body["day_used"] == 1
    assert body["month_used"] == 1
