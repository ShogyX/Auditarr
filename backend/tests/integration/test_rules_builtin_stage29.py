"""Stage 29 — Built-in rules: seeding + API protection.

Pins the operational contract:

  - ``register_builtin_rules`` is idempotent on repeat invocation
    (insert, refresh, unchanged, conflict counters).
  - It refreshes codebase-owned fields (description / definition)
    on existing builtins but never clobbers operator-controlled
    fields (enabled / priority / last_*).
  - It never silently promotes a custom rule with the same name
    to a builtin.
  - The list endpoint exposes ``is_builtin`` and supports the
    ``?is_builtin=true|false`` filter.
  - PATCH on a builtin rejects rename / description / definition
    (422) but accepts enabled / priority.
  - DELETE on a builtin is rejected (422); the operator-facing
    answer is "disable it".
  - Duplicate on a builtin produces a custom rule
    (``is_builtin: false``).
  - Export defaults to excluding builtins;
    ``include_builtins=true`` flips that.
  - Import refuses to overwrite a builtin even under
    ``on_conflict=overwrite``.
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
from app.models.rule import Rule
from app.models.user import User
from app.rules.builtin import BUILTIN_RULES, register_builtin_rules
from app.services.repositories import RuleRepository
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "rules-stage29.db"
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


# ── Seeding ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_builtin_rules_inserts_on_first_run(
    client: AsyncClient,
) -> None:
    """First invocation: every BUILTIN_RULES entry lands as a fresh
    row with is_builtin=True. Inserted count equals the builtin
    set size."""
    async with get_database().session() as sess:
        stats = await register_builtin_rules(sess)
    assert stats["inserted"] == len(BUILTIN_RULES)
    assert stats["refreshed"] == 0
    assert stats["unchanged"] == 0
    assert stats["conflicts"] == 0

    async with get_database().session() as sess:
        rules = await RuleRepository(sess).list_all()
    assert len(rules) == len(BUILTIN_RULES)
    assert all(r.is_builtin for r in rules)


@pytest.mark.asyncio
async def test_register_builtin_rules_is_idempotent(
    client: AsyncClient,
) -> None:
    """Second invocation: no inserts, no refreshes if the
    builtins didn't change. ``unchanged`` matches the set size."""
    async with get_database().session() as sess:
        await register_builtin_rules(sess)
    async with get_database().session() as sess:
        stats = await register_builtin_rules(sess)
    assert stats["inserted"] == 0
    assert stats["refreshed"] == 0
    assert stats["unchanged"] == len(BUILTIN_RULES)
    assert stats["conflicts"] == 0


@pytest.mark.asyncio
async def test_register_builtin_rules_refreshes_definition(
    client: AsyncClient,
) -> None:
    """When the codebase changes a builtin's definition between
    startups (simulated by mutating the stored row to look
    'stale'), the next register call refreshes it."""
    async with get_database().session() as sess:
        await register_builtin_rules(sess)

    # Mutate the stored definition to simulate a stale install.
    async with get_database().session() as sess:
        rules = await RuleRepository(sess).list_all()
        target = next(r for r in rules if r.name == BUILTIN_RULES[0].name)
        target.definition = {"match": {"all": [{"field": "size_bytes", "op": "gt", "value": 1}]}, "actions": [{"type": "add_tag", "tag": "stale"}]}
        target.description = "stale description"
        await sess.commit()

    async with get_database().session() as sess:
        stats = await register_builtin_rules(sess)
    assert stats["refreshed"] == 1
    # The rest were unchanged.
    assert stats["unchanged"] == len(BUILTIN_RULES) - 1

    # Confirm the refresh actually wrote the canonical version.
    async with get_database().session() as sess:
        rules = await RuleRepository(sess).list_all()
        target = next(r for r in rules if r.name == BUILTIN_RULES[0].name)
        assert target.description == BUILTIN_RULES[0].description
        assert target.definition == BUILTIN_RULES[0].definition


@pytest.mark.asyncio
async def test_register_builtin_rules_preserves_operator_enabled(
    client: AsyncClient,
) -> None:
    """If an operator has disabled a builtin (per-installation
    tuning), a re-seed must NOT re-enable it."""
    async with get_database().session() as sess:
        await register_builtin_rules(sess)
    async with get_database().session() as sess:
        rules = await RuleRepository(sess).list_all()
        # Pick a rule that ships enabled.
        target = next(
            r
            for r in rules
            if r.name == BUILTIN_RULES[0].name and r.enabled
        )
        target.enabled = False
        target.priority = 9999
        await sess.commit()

    async with get_database().session() as sess:
        await register_builtin_rules(sess)
    async with get_database().session() as sess:
        rules = await RuleRepository(sess).list_all()
        target = next(r for r in rules if r.name == BUILTIN_RULES[0].name)
    assert target.enabled is False
    assert target.priority == 9999


@pytest.mark.asyncio
async def test_register_builtin_rules_skips_custom_collision(
    client: AsyncClient,
) -> None:
    """An operator-created custom rule with the same name as a
    builtin must NOT be silently promoted. The collision is
    counted, the custom row stays untouched."""
    builtin_name = BUILTIN_RULES[0].name
    async with get_database().session() as sess:
        custom = Rule(
            name=builtin_name,
            description="operator's version",
            enabled=True,
            priority=42,
            definition={"match": {"all": [{"field": "is_orphaned", "op": "eq", "value": False}]}, "actions": [{"type": "add_tag", "tag": "operator"}]},
            is_builtin=False,
        )
        sess.add(custom)
        await sess.commit()

    async with get_database().session() as sess:
        stats = await register_builtin_rules(sess)
    assert stats["conflicts"] == 1

    async with get_database().session() as sess:
        rules = await RuleRepository(sess).list_all()
        target = next(r for r in rules if r.name == builtin_name)
    assert target.is_builtin is False
    assert target.description == "operator's version"
    assert target.priority == 42


# ── List endpoint filter ────────────────────────────────────


@pytest.mark.asyncio
async def test_list_rules_filters_by_is_builtin(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    # Seed the builtins.
    async with get_database().session() as sess:
        await register_builtin_rules(sess)
    # Also create one custom rule.
    create = await client.post(
        "/api/v1/rules",
        headers=headers,
        json={
            "name": "Custom test rule",
            "definition": {
                "match": {"all": [{"field": "size_bytes", "op": "gt", "value": 1000}]},
                "actions": [{"type": "add_tag", "tag": "big"}],
            },
        },
    )
    assert create.status_code == 201

    # Filter: builtins only.
    builtins_only = await client.get(
        "/api/v1/rules?is_builtin=true", headers=headers
    )
    assert all(r["is_builtin"] for r in builtins_only.json())
    assert len(builtins_only.json()) == len(BUILTIN_RULES)

    # Filter: custom only.
    custom_only = await client.get(
        "/api/v1/rules?is_builtin=false", headers=headers
    )
    assert all(not r["is_builtin"] for r in custom_only.json())
    assert len(custom_only.json()) == 1
    assert custom_only.json()[0]["name"] == "Custom test rule"

    # Default: everything.
    everything = await client.get("/api/v1/rules", headers=headers)
    assert len(everything.json()) == len(BUILTIN_RULES) + 1


# ── PATCH protection ────────────────────────────────────────


@pytest.mark.asyncio
async def test_patch_builtin_rejects_rename(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    async with get_database().session() as sess:
        await register_builtin_rules(sess)
    rules = (await client.get("/api/v1/rules?is_builtin=true", headers=headers)).json()
    target = rules[0]

    response = await client.patch(
        f"/api/v1/rules/{target['id']}",
        headers=headers,
        json={"name": "renamed by operator"},
    )
    assert response.status_code == 422
    assert "Cannot edit built-in rule fields" in response.json()["message"]


@pytest.mark.asyncio
async def test_patch_builtin_rejects_definition_change(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    async with get_database().session() as sess:
        await register_builtin_rules(sess)
    rules = (await client.get("/api/v1/rules?is_builtin=true", headers=headers)).json()
    target = rules[0]

    response = await client.patch(
        f"/api/v1/rules/{target['id']}",
        headers=headers,
        json={
            "definition": {
                "match": {"all": [{"field": "size_bytes", "op": "gt", "value": 1}]},
                "actions": [{"type": "add_tag", "tag": "tampered"}],
            }
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_patch_builtin_accepts_enabled_and_priority(
    client: AsyncClient,
) -> None:
    """The operator MUST be able to disable a builtin or reorder
    it; those are the legitimate per-installation knobs."""
    headers = await _admin_headers(client)
    async with get_database().session() as sess:
        await register_builtin_rules(sess)
    rules = (await client.get("/api/v1/rules?is_builtin=true", headers=headers)).json()
    target = next(r for r in rules if r["enabled"])

    response = await client.patch(
        f"/api/v1/rules/{target['id']}",
        headers=headers,
        json={"enabled": False, "priority": 200},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is False
    assert body["priority"] == 200
    assert body["is_builtin"] is True
    # And the name / definition didn't change.
    assert body["name"] == target["name"]


# ── DELETE protection ───────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_builtin_rejected(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    async with get_database().session() as sess:
        await register_builtin_rules(sess)
    rules = (await client.get("/api/v1/rules?is_builtin=true", headers=headers)).json()
    target = rules[0]

    response = await client.delete(
        f"/api/v1/rules/{target['id']}", headers=headers
    )
    assert response.status_code == 422
    # The rule must still exist after the rejected delete.
    after = await client.get(f"/api/v1/rules/{target['id']}", headers=headers)
    assert after.status_code == 200


# ── Duplicate produces custom ───────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_builtin_produces_custom_rule(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    async with get_database().session() as sess:
        await register_builtin_rules(sess)
    rules = (await client.get("/api/v1/rules?is_builtin=true", headers=headers)).json()
    target = rules[0]

    response = await client.post(
        f"/api/v1/rules/{target['id']}/duplicate", headers=headers
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["is_builtin"] is False
    assert body["name"].startswith(target["name"])
    assert body["name"] != target["name"]
    # The copy carries the same definition (operator can now edit
    # it freely).
    assert body["definition"] == target["definition"]


# ── Export / import ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_excludes_builtins_by_default(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    async with get_database().session() as sess:
        await register_builtin_rules(sess)
    # Plus one custom.
    await client.post(
        "/api/v1/rules",
        headers=headers,
        json={
            "name": "My custom",
            "definition": {
                "match": {"all": [{"field": "size_bytes", "op": "gt", "value": 1}]},
                "actions": [{"type": "add_tag", "tag": "x"}],
            },
        },
    )

    bundle = await client.get("/api/v1/rules/bundle/export", headers=headers)
    assert bundle.status_code == 200
    names = [r["name"] for r in bundle.json()["rules"]]
    assert "My custom" in names
    # No builtin name should appear.
    for spec in BUILTIN_RULES:
        assert spec.name not in names


@pytest.mark.asyncio
async def test_export_includes_builtins_when_requested(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    async with get_database().session() as sess:
        await register_builtin_rules(sess)

    bundle = await client.get(
        "/api/v1/rules/bundle/export?include_builtins=true",
        headers=headers,
    )
    assert bundle.status_code == 200
    names = [r["name"] for r in bundle.json()["rules"]]
    for spec in BUILTIN_RULES:
        assert spec.name in names


@pytest.mark.asyncio
async def test_import_refuses_to_overwrite_builtin(
    client: AsyncClient,
) -> None:
    """An import bundle that contains a name colliding with a
    builtin must NOT overwrite the builtin even under
    ``on_conflict=overwrite``."""
    headers = await _admin_headers(client)
    async with get_database().session() as sess:
        await register_builtin_rules(sess)

    builtin_name = BUILTIN_RULES[0].name
    builtin_def_before = BUILTIN_RULES[0].definition

    response = await client.post(
        "/api/v1/rules/bundle/import",
        headers=headers,
        json={
            "bundle": {
                "version": "1",
                "exported_at": "2026-05-12T00:00:00Z",
                "rules": [
                    {
                        "name": builtin_name,
                        "description": "operator's tampered version",
                        "enabled": True,
                        "priority": 100,
                        "definition": {
                            "match": {
                                "all": [
                                    {"field": "size_bytes", "op": "gt", "value": 1}
                                ]
                            },
                            "actions": [{"type": "add_tag", "tag": "tampered"}],
                        },
                    }
                ],
            },
            "on_conflict": "overwrite",
        },
    )
    assert response.status_code == 200
    body = response.json()
    outcomes = body["outcomes"]
    assert len(outcomes) == 1
    assert outcomes[0]["action"] == "skipped"
    assert "built-in" in outcomes[0]["error"].lower()

    # Confirm the builtin was NOT mutated.
    async with get_database().session() as sess:
        rules = await RuleRepository(sess).list_all()
        target = next(r for r in rules if r.name == builtin_name)
    assert target.definition == builtin_def_before
    assert target.is_builtin is True
