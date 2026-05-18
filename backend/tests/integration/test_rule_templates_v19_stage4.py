"""v1.9 Stage 4.4 — rule_templates table + API.

Pins:
  1. The seeder populates rule_templates from BUILTIN_RULES.
  2. The seeder is idempotent — second run reports unchanged.
  3. The seeder refreshes drift (description / definition change).
  4. A deleted template row is re-inserted on the next seed run.
  5. GET /api/v1/rule-templates lists all templates ordered by
     priority asc.
  6. POST /api/v1/rule-templates/{id}/use creates a Rule from
     the template's body (is_builtin=False, enabled=True).
  7. Name collision: a second "use" of the same template yields
     a "Name (copy)" rule rather than a 409.
  8. Unknown template id returns 404.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.rule import Rule
from app.models.rule_template import RuleTemplate
from app.models.user import User
from app.rules.builtin import BUILTIN_RULES, register_builtin_templates
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "templates.db"
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
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
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


# ── Seeder ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_seeder_inserts_all_builtins(client: AsyncClient) -> None:
    async with get_database().session() as sess:
        stats = await register_builtin_templates(sess)
    assert stats["inserted"] == len(BUILTIN_RULES)
    assert stats["refreshed"] == 0
    assert stats["unchanged"] == 0
    async with get_database().session() as sess:
        rows = (await sess.execute(select(RuleTemplate))).scalars().all()
        assert len(rows) == len(BUILTIN_RULES)


@pytest.mark.asyncio
async def test_seeder_is_idempotent(client: AsyncClient) -> None:
    async with get_database().session() as sess:
        await register_builtin_templates(sess)
    async with get_database().session() as sess:
        stats2 = await register_builtin_templates(sess)
    assert stats2["inserted"] == 0
    assert stats2["unchanged"] == len(BUILTIN_RULES)


@pytest.mark.asyncio
async def test_seeder_refreshes_on_drift(client: AsyncClient) -> None:
    """If a template's stored description differs from the
    codebase, the seeder updates it."""
    async with get_database().session() as sess:
        await register_builtin_templates(sess)
    # Mutate one row's description so the next seed sees drift.
    async with get_database().session() as sess:
        first_name = BUILTIN_RULES[0].name
        await sess.execute(
            update(RuleTemplate)
            .where(RuleTemplate.name == first_name)
            .values(description="forced-drift")
        )
        await sess.commit()
    async with get_database().session() as sess:
        stats = await register_builtin_templates(sess)
    assert stats["refreshed"] == 1


@pytest.mark.asyncio
async def test_deleted_template_restored_on_next_seed(
    client: AsyncClient,
) -> None:
    """Operator's "Restore deleted built-ins" path: deleting a
    template row → re-inserted on the next startup seed."""
    async with get_database().session() as sess:
        await register_builtin_templates(sess)
    async with get_database().session() as sess:
        from sqlalchemy import delete

        await sess.execute(
            delete(RuleTemplate).where(
                RuleTemplate.name == BUILTIN_RULES[0].name
            )
        )
        await sess.commit()
    async with get_database().session() as sess:
        stats = await register_builtin_templates(sess)
    assert stats["inserted"] == 1


# ── API ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_templates_returns_seeded_set(client: AsyncClient) -> None:
    async with get_database().session() as sess:
        await register_builtin_templates(sess)
    headers = await _admin_headers(client)
    r = await client.get("/api/v1/rule-templates", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == len(BUILTIN_RULES)
    # Priority-asc ordering (lower first).
    priorities = [row["priority"] for row in body]
    assert priorities == sorted(priorities)


@pytest.mark.asyncio
async def test_use_template_creates_rule(client: AsyncClient) -> None:
    async with get_database().session() as sess:
        await register_builtin_templates(sess)
        first = (
            await sess.execute(
                select(RuleTemplate).order_by(RuleTemplate.name).limit(1)
            )
        ).scalar_one()
        template_id = first.id
        expected_name = first.name
        expected_def = first.definition

    headers = await _admin_headers(client)
    r = await client.post(
        f"/api/v1/rule-templates/{template_id}/use", headers=headers
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == expected_name
    assert body["is_builtin"] is False
    assert body["enabled"] is True
    assert body["definition"] == expected_def


@pytest.mark.asyncio
async def test_use_template_twice_appends_copy_suffix(
    client: AsyncClient,
) -> None:
    """Second use of the same template yields a uniquely-named Rule."""
    async with get_database().session() as sess:
        await register_builtin_templates(sess)
        first = (
            await sess.execute(
                select(RuleTemplate).order_by(RuleTemplate.name).limit(1)
            )
        ).scalar_one()
        template_id = first.id
        base_name = first.name

    headers = await _admin_headers(client)
    r1 = await client.post(
        f"/api/v1/rule-templates/{template_id}/use", headers=headers
    )
    r2 = await client.post(
        f"/api/v1/rule-templates/{template_id}/use", headers=headers
    )
    r3 = await client.post(
        f"/api/v1/rule-templates/{template_id}/use", headers=headers
    )
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r3.status_code == 201
    assert r1.json()["name"] == base_name
    assert r2.json()["name"] == f"{base_name} (copy)"
    assert r3.json()["name"] == f"{base_name} (copy 2)"
    # Three distinct Rule rows materialized.
    async with get_database().session() as sess:
        rows = (
            await sess.execute(
                select(Rule).where(Rule.name.like(f"{base_name}%"))
            )
        ).scalars().all()
        assert len(rows) == 3


@pytest.mark.asyncio
async def test_use_template_unknown_id_returns_404(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    r = await client.post(
        "/api/v1/rule-templates/does-not-exist/use", headers=headers
    )
    assert r.status_code == 404
