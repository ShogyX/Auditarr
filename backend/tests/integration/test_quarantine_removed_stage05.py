"""Stage 05 (v1.7) — quarantine surface removed from the API.

Plan addendum §A.0: "UI/API surfaces lose the quarantine toggle."

Two regression guards:

  1. The four pre-Stage-05 endpoints — POST
     ``/media/{id}/quarantine``, POST ``/media/{id}/unquarantine``,
     POST ``/media/bulk/quarantine``, POST ``/media/bulk/unquarantine``
     — return 404 / 405 (gone) instead of accepting requests.
  2. The ``quarantined``, ``quarantined_at``, ``quarantined_reason``
     fields are absent from ``MediaFileSummary`` / ``MediaFileDetail``
     response bodies; a Stage 05 client that didn't get the memo
     can't accidentally read a stale field.
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

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "stage05_quarantine_gone.db"
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
            "email": "admin@example.com",
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


async def _seed_one_file() -> str:
    """Seed a single media file directly into the DB. Returns its id."""
    async with get_database().session() as sess:
        lib = Library(name="L", root_path="/lib", kind="movies")
        sess.add(lib)
        await sess.flush()
        mf = MediaFile(
            library_id=lib.id,
            path="/lib/x.mkv",
            relative_path="x.mkv",
            filename="x.mkv",
            extension="mkv",
            category="media",
            severity="ok",
            severity_rank=0,
            size_bytes=10,
            mtime=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
            seen_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
            has_subtitles=False,
            is_orphaned=False,
        )
        sess.add(mf)
        await sess.commit()
        return mf.id


@pytest.mark.asyncio
async def test_per_file_quarantine_endpoint_is_gone(
    client: AsyncClient,
) -> None:
    """``POST /media/{id}/quarantine`` returned 200 in Stage 27.
    Stage 05 removed the route; the response must be 404 or 405
    (FastAPI returns 405 if a sibling route — e.g. /reprobe —
    matches the same path prefix and just doesn't accept the
    method)."""
    headers = await _admin_headers(client)
    media_id = await _seed_one_file()
    resp = await client.post(
        f"/api/v1/media/{media_id}/quarantine",
        headers=headers,
        json={"reason": "should fail — route is gone"},
    )
    assert resp.status_code in (404, 405), (
        f"expected 404/405 for retired quarantine route, got "
        f"{resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_per_file_unquarantine_endpoint_is_gone(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    media_id = await _seed_one_file()
    resp = await client.post(
        f"/api/v1/media/{media_id}/unquarantine", headers=headers
    )
    assert resp.status_code in (404, 405)


@pytest.mark.asyncio
async def test_bulk_quarantine_endpoint_is_gone(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    media_id = await _seed_one_file()
    resp = await client.post(
        "/api/v1/media/bulk/quarantine",
        headers=headers,
        json={"media_ids": [media_id], "reason": "test"},
    )
    assert resp.status_code in (404, 405)


@pytest.mark.asyncio
async def test_bulk_unquarantine_endpoint_is_gone(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    media_id = await _seed_one_file()
    resp = await client.post(
        "/api/v1/media/bulk/unquarantine",
        headers=headers,
        json={"media_ids": [media_id]},
    )
    assert resp.status_code in (404, 405)


@pytest.mark.asyncio
async def test_media_list_response_has_no_quarantined_field(
    client: AsyncClient,
) -> None:
    """The list endpoint's items used to carry a ``quarantined``
    bool. Stage 05 removed it from the schema; an item must not
    expose the field even at the JSON level."""
    headers = await _admin_headers(client)
    await _seed_one_file()
    resp = await client.get("/api/v1/media", headers=headers)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert "quarantined" not in item, (
        f"unexpected 'quarantined' field on /media item: {item}"
    )


@pytest.mark.asyncio
async def test_media_detail_response_has_no_quarantine_fields(
    client: AsyncClient,
) -> None:
    """``MediaFileDetail`` used to expose ``quarantined``,
    ``quarantined_at``, ``quarantined_reason``. None should be
    present in the JSON body now."""
    headers = await _admin_headers(client)
    media_id = await _seed_one_file()
    resp = await client.get(f"/api/v1/media/{media_id}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    for forbidden in ("quarantined", "quarantined_at", "quarantined_reason"):
        assert forbidden not in body, (
            f"unexpected '{forbidden}' field on detail body: {body}"
        )


@pytest.mark.asyncio
async def test_media_list_rejects_quarantined_query_param_silently(
    client: AsyncClient,
) -> None:
    """Stage 27 honoured ``?quarantined=true`` and
    ``?include_quarantined=true``. Stage 05 removed both params.
    FastAPI's default behaviour for an unknown query param is to
    accept-and-ignore (extra params don't fail validation by
    default). The contract we DO assert: the response is still a
    valid page (200) and doesn't suddenly behave like a filter."""
    headers = await _admin_headers(client)
    await _seed_one_file()
    resp = await client.get(
        "/api/v1/media?quarantined=true&include_quarantined=true",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    # The single seeded file still surfaces (the bogus params
    # don't filter it out).
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_rule_with_quarantine_action_in_body_returns_422(
    client: AsyncClient,
) -> None:
    """Creating a rule via the API with a ``type: "quarantine"``
    action must fail validation — the Action union no longer
    accepts that literal. The 0015 migration handles in-place
    persisted bodies; the API rejects new bodies that try to
    re-introduce the action."""
    headers = await _admin_headers(client)
    resp = await client.post(
        "/api/v1/rules",
        headers=headers,
        json={
            "name": "tries to quarantine",
            "description": "should not validate",
            "enabled": True,
            "priority": 50,
            "definition": {
                "match": {"field": "extension", "op": "eq", "value": "mkv"},
                "actions": [{"type": "quarantine", "reason": "x"}],
            },
        },
    )
    assert resp.status_code in (400, 422), resp.text


@pytest.mark.asyncio
async def test_rule_with_delete_confirm_in_body_returns_422(
    client: AsyncClient,
) -> None:
    """Same regression guard for the retired ``confirm`` flag on
    Delete. The migration scrubs it from persisted bodies; new
    bodies fail validation."""
    headers = await _admin_headers(client)
    resp = await client.post(
        "/api/v1/rules",
        headers=headers,
        json={
            "name": "tries old confirm",
            "description": "should not validate",
            "enabled": True,
            "priority": 50,
            "definition": {
                "match": {"field": "extension", "op": "eq", "value": "mkv"},
                "actions": [{"type": "delete", "confirm": True}],
            },
        },
    )
    assert resp.status_code in (400, 422), resp.text


@pytest.mark.asyncio
async def test_rule_vocabulary_has_delete_with_reason_only(
    client: AsyncClient,
) -> None:
    """The visible vocabulary surface for the Delete action no
    longer publishes ``confirm``; it publishes ``reason`` only."""
    headers = await _admin_headers(client)
    resp = await client.get("/api/v1/rules/vocabulary", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    delete_action = next(a for a in body["actions"] if a["type"] == "delete")
    args = delete_action["args_schema"]
    assert "reason" in args
    assert "confirm" not in args, (
        f"Delete action vocabulary still publishes 'confirm': {args}"
    )
