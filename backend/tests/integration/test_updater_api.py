"""Updater API integration tests.

We patch the feed transport so checks complete deterministically,
verify the apply path actually writes the sentinel file the host helper
will watch for, and exercise the status-file consumption + rollback.
"""

from __future__ import annotations

import json
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
from app.models.update_check import UpdateCheck
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
    db_path = tmp_path / "updater_api.db"
    sentinel_path = tmp_path / "updater" / "apply.request"
    status_path = tmp_path / "updater" / "apply.status"

    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    monkeypatch.setenv("AUDITARR_APP_VERSION", "1.0.0")
    monkeypatch.setenv(
        "AUDITARR_UPDATE_FEED_URL", "https://example.test/feed"
    )
    monkeypatch.setenv("AUDITARR_UPDATE_APPLY_SENTINEL", str(sentinel_path))
    monkeypatch.setenv(
        "AUDITARR_UPDATE_APPLY_STATUS_PATH", str(status_path)
    )
    # Stage 19: existing tests predate install-mode gating; they
    # exercise the sentinel/apply flow which is now bare-metal-only.
    # v1.9.1 Stage 1.6 moved this from "docker" to "bare-metal"
    # because the in-container apply path was removed — Docker installs
    # now return a manual command set instead.
    monkeypatch.setenv("AUDITARR_UPDATE_INSTALL_MODE", "bare-metal")

    from app.core.settings import get_settings
    from app.updater.install_mode import reset_cache_for_tests

    get_settings.cache_clear()
    reset_cache_for_tests()

    # Default httpx transport: the feed returns 1.4.0 + a body. Individual
    # tests can swap this out via the `feed_transport` attribute on the
    # client object.
    captured: dict = {"body": {"tag_name": "v1.4.0", "body": "Bug fixes."}}

    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        # IMPORTANT: only override the transport when the caller hasn't
        # supplied one. The pytest fixture itself uses an ASGITransport
        # to talk to the FastAPI app — if we replace that, every API
        # call short-circuits to the mocked feed and the test fails
        # with a confusing "auth/register returned 200 with no id".
        if "transport" not in kwargs:

            def handler(request: httpx.Request) -> httpx.Response:
                if isinstance(captured.get("exc"), Exception):
                    raise captured["exc"]
                return httpx.Response(
                    captured.get("status", 200), json=captured["body"]
                )

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
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            c._feed_state = captured  # type: ignore[attr-defined]
            c._sentinel_path = sentinel_path  # type: ignore[attr-defined]
            c._status_path = status_path  # type: ignore[attr-defined]
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
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "a@example.com",
            "username": "admin",
            "password": PASSWORD,
        },
    )
    user_id = response.json()["id"]
    async with get_database().session() as sess:
        await sess.execute(
            update(User).where(User.id == user_id).values(role="admin")
        )
        await sess.commit()
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "admin", "password": PASSWORD},
    )
    return (
        {"authorization": f"Bearer {login.json()['access_token']}"},
        user_id,
    )


# ── Status ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_status_when_no_check_yet(client: AsyncClient) -> None:
    headers, _ = await _admin_headers(client)
    response = await client.get("/api/v1/updater/status", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["installed_version"] == "1.0.0"
    assert body["latest_version"] is None
    assert body["has_update"] is False
    assert body["last_checked_at"] is None
    assert body["apply_in_progress"] is False
    assert body["feed_url"] == "https://example.test/feed"


# ── Force check ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_check_writes_row_and_emits_update_available(
    client: AsyncClient,
) -> None:
    headers, _ = await _admin_headers(client)
    bus = get_event_bus()
    events_seen: list[str] = []

    async def listener(event) -> None:
        events_seen.append(event.name)

    bus.subscribe("update.available", listener)

    response = await client.post("/api/v1/updater/check", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["latest_version"] == "1.4.0"
    assert body["changelog"] == "Bug fixes."

    # The check row should be in the DB.
    async with get_database().session() as sess:
        from sqlalchemy import select

        rows = (
            await sess.execute(select(UpdateCheck))
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].latest_version == "1.4.0"

    # Status should now reflect "update available".
    status = await client.get("/api/v1/updater/status", headers=headers)
    s = status.json()
    assert s["has_update"] is True
    assert s["latest_version"] == "1.4.0"

    # The update.available event fired.
    assert "update.available" in events_seen


@pytest.mark.asyncio
async def test_check_records_failure_when_feed_unreachable(
    client: AsyncClient,
) -> None:
    headers, _ = await _admin_headers(client)
    client._feed_state["exc"] = httpx.ConnectError("dns gone")  # type: ignore[attr-defined]

    response = await client.post("/api/v1/updater/check", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "unreachable" in (body["detail"] or "").lower()


# ── Apply path: sentinel + status ──────────────────────────────
@pytest.mark.asyncio
async def test_apply_writes_sentinel_and_creates_row(
    client: AsyncClient,
) -> None:
    headers, user_id = await _admin_headers(client)
    response = await client.post(
        "/api/v1/updater/apply",
        headers=headers,
        json={"to_version": "1.4.0"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "requested"
    assert body["to_version"] == "1.4.0"
    assert body["from_version"] == "1.0.0"
    assert body["triggered_by_user_id"] == user_id

    sentinel_path = client._sentinel_path  # type: ignore[attr-defined]
    assert sentinel_path.exists()
    payload = json.loads(sentinel_path.read_text())
    assert payload["to_version"] == "1.4.0"
    assert payload["from_version"] == "1.0.0"
    assert payload["apply_id"] == body["id"]

    # Status now reports apply_in_progress.
    status = await client.get("/api/v1/updater/status", headers=headers)
    assert status.json()["apply_in_progress"] is True


@pytest.mark.asyncio
async def test_apply_rejected_when_one_in_progress(
    client: AsyncClient,
) -> None:
    headers, _ = await _admin_headers(client)
    first = await client.post(
        "/api/v1/updater/apply",
        headers=headers,
        json={"to_version": "1.4.0"},
    )
    assert first.status_code == 201

    second = await client.post(
        "/api/v1/updater/apply",
        headers=headers,
        json={"to_version": "1.4.1"},
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_status_file_transitions_open_apply(
    client: AsyncClient,
) -> None:
    """Simulate the host helper writing back a completion status."""
    headers, _ = await _admin_headers(client)
    apply_resp = await client.post(
        "/api/v1/updater/apply",
        headers=headers,
        json={"to_version": "1.4.0"},
    )
    apply_id = apply_resp.json()["id"]

    status_path: Path = client._status_path  # type: ignore[attr-defined]
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(
        json.dumps(
            {
                "apply_id": apply_id,
                "status": "completed",
                "detail": "pulled image, recreated container",
            }
        )
    )

    # Drive the poll loop manually.
    from app.core.settings import get_settings
    from app.updater import UpdaterService

    async with get_database().session() as sess:
        service = UpdaterService(
            session=sess,
            settings=get_settings(),
            event_bus=get_event_bus(),
        )
        applied = await service.poll_apply_status()

    assert applied is not None
    assert applied.id == apply_id
    assert applied.status == "completed"
    assert "pulled image" in (applied.detail or "")

    # The status file should be consumed (deleted).
    assert not status_path.exists()


# ── Rollback ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_rollback_marks_old_and_creates_new(
    client: AsyncClient,
) -> None:
    headers, _ = await _admin_headers(client)
    apply_resp = await client.post(
        "/api/v1/updater/apply",
        headers=headers,
        json={"to_version": "1.4.0"},
    )
    apply_id = apply_resp.json()["id"]

    # Mark the apply completed (bypassing the host helper for the test).
    async with get_database().session() as sess:
        row = await sess.get(UpdateApply, apply_id)
        assert row is not None
        row.status = "completed"
        row.finished_at = utcnow()
        await sess.commit()

    rollback = await client.post(
        f"/api/v1/updater/applies/{apply_id}/rollback", headers=headers
    )
    assert rollback.status_code == 200, rollback.text
    body = rollback.json()
    # The rollback returned the NEW request (targeting from_version=1.0.0).
    assert body["to_version"] == "1.0.0"
    assert body["status"] == "requested"

    # Original row is marked rolled_back.
    async with get_database().session() as sess:
        original = await sess.get(UpdateApply, apply_id)
        assert original is not None
        assert original.status == "rolled_back"


@pytest.mark.asyncio
async def test_rollback_requires_completed_status(
    client: AsyncClient,
) -> None:
    headers, _ = await _admin_headers(client)
    apply_resp = await client.post(
        "/api/v1/updater/apply",
        headers=headers,
        json={"to_version": "1.4.0"},
    )
    apply_id = apply_resp.json()["id"]
    # Still in "requested" state — rollback should refuse.
    rollback = await client.post(
        f"/api/v1/updater/applies/{apply_id}/rollback", headers=headers
    )
    assert rollback.status_code == 422


@pytest.mark.asyncio
async def test_rollback_unknown_id_is_404(client: AsyncClient) -> None:
    headers, _ = await _admin_headers(client)
    response = await client.post(
        "/api/v1/updater/applies/no-such-id/rollback", headers=headers
    )
    assert response.status_code == 404


# ── Listing endpoints ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_list_checks_and_applies(client: AsyncClient) -> None:
    headers, _ = await _admin_headers(client)
    await client.post("/api/v1/updater/check", headers=headers)
    await client.post(
        "/api/v1/updater/apply",
        headers=headers,
        json={"to_version": "1.4.0"},
    )

    checks = await client.get("/api/v1/updater/checks", headers=headers)
    assert checks.status_code == 200
    assert len(checks.json()) == 1

    applies = await client.get("/api/v1/updater/applies", headers=headers)
    assert applies.status_code == 200
    assert len(applies.json()) == 1
    assert applies.json()[0]["to_version"] == "1.4.0"


# ── Stage 19: install-mode gating on apply ───────────────────
@pytest.mark.asyncio
async def test_status_exposes_install_mode_and_apply_enabled(
    client: AsyncClient,
) -> None:
    """The new status fields are populated and consistent."""
    headers, _ = await _admin_headers(client)

    r = await client.get("/api/v1/updater/status", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "install_mode" in body
    assert "apply_enabled" in body
    # The fixture pins install_mode=bare-metal so apply must be enabled.
    assert body["install_mode"] == "bare-metal"
    assert body["apply_enabled"] is True
    # Bare-metal doesn't surface a manual command set — the UI's Apply
    # button does the work via the watcher.
    assert body.get("manual_apply_command") is None


@pytest.mark.asyncio
async def test_apply_refused_when_install_mode_unmanaged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force install_mode=unmanaged and assert apply returns 409."""
    # We rebuild the fixture inline so we can pin install_mode to
    # unmanaged without disturbing the shared client fixture above.
    from httpx import ASGITransport, AsyncClient as _Async
    from app.core.settings import get_settings
    from app.updater.install_mode import reset_cache_for_tests

    db_path = tmp_path / "u.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    monkeypatch.setenv("AUDITARR_APP_VERSION", "1.0.0")
    monkeypatch.setenv(
        "AUDITARR_UPDATE_FEED_URL", "https://example.test/feed"
    )
    monkeypatch.setenv(
        "AUDITARR_UPDATE_APPLY_SENTINEL", str(tmp_path / "apply.request")
    )
    monkeypatch.setenv(
        "AUDITARR_UPDATE_APPLY_STATUS_PATH", str(tmp_path / "apply.status")
    )
    monkeypatch.setenv("AUDITARR_UPDATE_INSTALL_MODE", "unmanaged")
    get_settings.cache_clear()
    reset_cache_for_tests()

    from app.main import create_app
    from app.storage.database import get_database

    app = create_app()
    db = get_database()
    db._engine = None  # type: ignore[attr-defined]
    db._sessionmaker = None  # type: ignore[attr-defined]
    await db.connect()
    from app.storage.base import Base
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with _Async(transport=transport, base_url="http://t") as c:
        # Register + login an admin.
        await c.post(
            "/api/v1/auth/register",
            json={
                "email": "admin@example.com",
                "username": "admin",
                "password": "supersecret-password-1!",
            },
        )
        login = await c.post(
            "/api/v1/auth/login",
            json={"login": "admin", "password": "supersecret-password-1!"},
        )
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Promote to admin via direct DB write since the test app has no
        # bootstrap admin env vars set.
        from app.models.user import User
        from sqlalchemy import select
        async with db.session() as session:
            user = (
                await session.execute(
                    select(User).where(User.username == "admin")
                )
            ).scalar_one()
            user.role = "admin"
            await session.commit()

        # Status reports unmanaged + apply disabled.
        status = await c.get("/api/v1/updater/status", headers=headers)
        body = status.json()
        assert body["install_mode"] == "unmanaged"
        assert body["apply_enabled"] is False

        # Apply request gets a 409 with the unmanaged error message.
        apply = await c.post(
            "/api/v1/updater/apply",
            headers=headers,
            json={"to_version": "9.9.9"},
        )
        assert apply.status_code == 409
        body = apply.json()
        # The error envelope uses ``message`` (not ``detail``) per the
        # app's standard error shape.
        assert "unmanaged" in body.get("message", "").lower()

    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await db.disconnect()
    get_settings.cache_clear()
    reset_cache_for_tests()


@pytest.mark.asyncio
async def test_apply_refused_when_install_mode_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stage 1.6 (v1.9.1) — Docker installs no longer auto-apply.

    The status endpoint must report ``apply_enabled=False`` and surface
    ``manual_apply_command`` with the host commands. ``POST /apply``
    must reject with 409 and a Docker-specific error message.
    """
    from httpx import ASGITransport, AsyncClient as _Async
    from app.core.settings import get_settings
    from app.updater.install_mode import reset_cache_for_tests

    db_path = tmp_path / "u.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    monkeypatch.setenv("AUDITARR_APP_VERSION", "1.0.0")
    monkeypatch.setenv(
        "AUDITARR_UPDATE_FEED_URL", "https://example.test/feed"
    )
    monkeypatch.setenv(
        "AUDITARR_UPDATE_APPLY_SENTINEL", str(tmp_path / "apply.request")
    )
    monkeypatch.setenv(
        "AUDITARR_UPDATE_APPLY_STATUS_PATH", str(tmp_path / "apply.status")
    )
    monkeypatch.setenv("AUDITARR_UPDATE_INSTALL_MODE", "docker")
    get_settings.cache_clear()
    reset_cache_for_tests()

    from app.main import create_app
    from app.storage.database import get_database

    app = create_app()
    db = get_database()
    db._engine = None  # type: ignore[attr-defined]
    db._sessionmaker = None  # type: ignore[attr-defined]
    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with _Async(transport=transport, base_url="http://t") as c:
        await c.post(
            "/api/v1/auth/register",
            json={
                "email": "admin@example.com",
                "username": "admin",
                "password": "supersecret-password-1!",
            },
        )
        login = await c.post(
            "/api/v1/auth/login",
            json={"login": "admin", "password": "supersecret-password-1!"},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        from sqlalchemy import select
        async with db.session() as session:
            user = (
                await session.execute(
                    select(User).where(User.username == "admin")
                )
            ).scalar_one()
            user.role = "admin"
            await session.commit()

        status = await c.get("/api/v1/updater/status", headers=headers)
        body = status.json()
        assert body["install_mode"] == "docker"
        assert body["apply_enabled"] is False
        # Operator gets a copy-paste-ready command set in the panel.
        assert body["manual_apply_command"] is not None
        assert "docker compose pull" in body["manual_apply_command"]
        assert "git pull" in body["manual_apply_command"]
        assert "force-recreate" in body["manual_apply_command"]

        apply = await c.post(
            "/api/v1/updater/apply",
            headers=headers,
            json={"to_version": "9.9.9"},
        )
        assert apply.status_code == 409
        assert "docker" in apply.json().get("message", "").lower()

    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await db.disconnect()
    get_settings.cache_clear()
    reset_cache_for_tests()
