"""Stage 06 (v1.7) — vocabulary endpoint surfaces the new DSL bits.

The visual rule builder reads ``/rules/vocabulary`` to render its
inputs. Stage 06 adds:

  1. ``vt_status`` field with the canonical literal enum.
  2. ``probe_failed`` field as a bool.
  3. Notify action's ``args_schema`` carries a ``throttle`` object
     advertising the two sub-properties (window_seconds,
     max_per_window) with their minimum constraints.
  4. ``rule_flags.acknowledged_destructive`` entry for the builder
     to render the "I understand this rule deletes files" checkbox.

This test is the Layer 6 regression guard.
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
from app.models.user import User
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "stage06_vocab.db"
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


@pytest.mark.asyncio
async def test_vocabulary_publishes_vt_status_field_with_enum(
    client: AsyncClient,
) -> None:
    """``vt_status`` is a string field with a fixed enum (the
    Stage 06 canonical literal set)."""
    headers = await _admin_headers(client)
    resp = await client.get("/api/v1/rules/vocabulary", headers=headers)
    assert resp.status_code == 200
    body = resp.json()

    field = next(
        (f for f in body["fields"] if f["key"] == "vt_status"), None
    )
    assert field is not None, "vt_status field missing from vocabulary"
    assert field["type"] == "string"
    assert field["enum"] is not None
    # The literal set from VT_STATUS_VALUES, sorted.
    assert set(field["enum"]) == {
        "clean", "malicious", "suspicious", "not_found", "error"
    }


@pytest.mark.asyncio
async def test_vocabulary_publishes_probe_failed_as_bool_field(
    client: AsyncClient,
) -> None:
    """``probe_failed`` is a bool field; the builder renders bool
    ops (eq, ne) for it."""
    headers = await _admin_headers(client)
    resp = await client.get("/api/v1/rules/vocabulary", headers=headers)
    body = resp.json()

    field = next(
        (f for f in body["fields"] if f["key"] == "probe_failed"), None
    )
    assert field is not None
    assert field["type"] == "bool"
    # Bool ops are eq + ne.
    assert "eq" in body["ops"]["bool"]
    assert "ne" in body["ops"]["bool"]


@pytest.mark.asyncio
async def test_notify_action_publishes_throttle_args(
    client: AsyncClient,
) -> None:
    """The Notify action's args_schema carries the throttle
    object with both sub-properties + their constraints."""
    headers = await _admin_headers(client)
    resp = await client.get("/api/v1/rules/vocabulary", headers=headers)
    body = resp.json()

    notify = next(a for a in body["actions"] if a["type"] == "notify")
    args = notify["args_schema"]
    assert "throttle" in args
    throttle = args["throttle"]
    assert throttle["required"] is False
    # Object-typed arg with nested properties.
    assert throttle["type"] == "object"
    props = throttle["properties"]
    assert "window_seconds" in props
    assert "max_per_window" in props
    # Minimums match the schema constraints (plan §352).
    assert props["window_seconds"]["minimum"] == 60
    assert props["max_per_window"]["minimum"] == 1


@pytest.mark.asyncio
async def test_vocabulary_publishes_acknowledged_destructive_rule_flag(
    client: AsyncClient,
) -> None:
    """``rule_flags.acknowledged_destructive`` advertises the
    checkbox the builder must render when a rule contains a
    delete action (per addendum A.0.1)."""
    headers = await _admin_headers(client)
    resp = await client.get("/api/v1/rules/vocabulary", headers=headers)
    body = resp.json()

    assert "rule_flags" in body
    ack = body["rule_flags"].get("acknowledged_destructive")
    assert ack is not None, "acknowledged_destructive flag missing"
    assert ack["type"] == "bool"
    # The label is the operator-facing checkbox text per addendum.
    assert "delete" in ack["label"].lower()
    # ``required_when`` describes the visibility/requirement rule
    # the builder uses to decide when to show + enforce the check.
    assert ack["required_when"] == {"any_action_type": "delete"}


@pytest.mark.asyncio
async def test_vocabulary_action_set_unchanged_post_stage_06(
    client: AsyncClient,
) -> None:
    """Stage 06 doesn't add a new action type — the union is
    still {set_severity, add_tag, queue_optimization, notify,
    delete}. Throttle and ack are extensions of existing
    actions / rule-level metadata, not new actions."""
    headers = await _admin_headers(client)
    resp = await client.get("/api/v1/rules/vocabulary", headers=headers)
    body = resp.json()
    types = {a["type"] for a in body["actions"]}
    assert types == {
        "set_severity",
        "add_tag",
        "queue_optimization",
        "notify",
        "delete",
    }
