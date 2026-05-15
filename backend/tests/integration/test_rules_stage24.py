"""Stage 24 — Rules duplicate / export / import endpoints.

Pins the additive contract:

  - ``POST /api/v1/rules/{id}/duplicate`` creates a disabled copy
    with a guaranteed-unique name; the original is untouched.
  - ``GET /api/v1/rules/bundle/export`` returns a portable bundle
    without volatile state.
  - ``POST /api/v1/rules/bundle/import`` round-trips the bundle and
    handles the three conflict strategies cleanly, reporting one
    outcome per entry rather than failing the whole batch on a
    single bad rule.

Volume here is bounded by what the UI cares about, not exhaustive
proofs about the rules service — those each live in
``test_rules_api.py`` and ``test_rules_evaluator.py`` already.
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
    db_path = tmp_path / "rules-stage24.db"
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


async def _non_admin_headers(client: AsyncClient) -> dict[str, str]:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "u@example.com",
            "username": "user1",
            "password": PASSWORD,
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"login": "user1", "password": PASSWORD},
    )
    return {"authorization": f"Bearer {login.json()['access_token']}"}


def _rule_body(name: str = "HEVC media") -> dict:
    return {
        "name": name,
        "description": "Tag HEVC media files",
        "priority": 100,
        "enabled": True,
        "definition": {
            "match": {
                "all": [{"field": "video_codec", "op": "eq", "value": "hevc"}]
            },
            "actions": [{"type": "set_severity", "severity": "info"}],
        },
    }


# ── Duplicate ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_creates_disabled_copy(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    create = await client.post("/api/v1/rules", headers=headers, json=_rule_body())
    assert create.status_code == 201, create.text
    rule_id = create.json()["id"]

    dup = await client.post(
        f"/api/v1/rules/{rule_id}/duplicate", headers=headers
    )
    assert dup.status_code == 201, dup.text
    body = dup.json()
    assert body["name"] == "HEVC media (copy)"
    assert body["enabled"] is False  # copies start disabled
    assert body["description"] == "Tag HEVC media files"
    assert body["definition"]["actions"][0]["type"] == "set_severity"
    # Original is untouched.
    original = await client.get(f"/api/v1/rules/{rule_id}", headers=headers)
    assert original.json()["enabled"] is True


@pytest.mark.asyncio
async def test_duplicate_handles_repeated_collisions(client: AsyncClient) -> None:
    """Duplicating the same rule three times should yield
    ``(copy)``, ``(copy 2)``, ``(copy 3)`` — exercising the
    increment loop, not just the first-collision branch."""
    headers = await _admin_headers(client)
    create = await client.post("/api/v1/rules", headers=headers, json=_rule_body())
    rule_id = create.json()["id"]

    names: list[str] = []
    for _ in range(3):
        dup = await client.post(
            f"/api/v1/rules/{rule_id}/duplicate", headers=headers
        )
        assert dup.status_code == 201
        names.append(dup.json()["name"])

    assert names == [
        "HEVC media (copy)",
        "HEVC media (copy 2)",
        "HEVC media (copy 3)",
    ]


@pytest.mark.asyncio
async def test_duplicate_unknown_rule_404(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    response = await client.post(
        "/api/v1/rules/does-not-exist/duplicate", headers=headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_duplicate_admin_only(client: AsyncClient) -> None:
    admin_headers = await _admin_headers(client)
    create = await client.post(
        "/api/v1/rules", headers=admin_headers, json=_rule_body()
    )
    rule_id = create.json()["id"]

    user_headers = await _non_admin_headers(client)
    response = await client.post(
        f"/api/v1/rules/{rule_id}/duplicate", headers=user_headers
    )
    assert response.status_code == 403


# ── Export ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_returns_portable_bundle(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    await client.post("/api/v1/rules", headers=headers, json=_rule_body("Rule A"))
    await client.post("/api/v1/rules", headers=headers, json=_rule_body("Rule B"))

    response = await client.get("/api/v1/rules/bundle/export", headers=headers)
    assert response.status_code == 200, response.text
    bundle = response.json()
    assert bundle["version"] == "1"
    assert "exported_at" in bundle
    assert {r["name"] for r in bundle["rules"]} == {"Rule A", "Rule B"}

    # Volatile state must not be in the export — the bundle is meant
    # to be content-addressable. id and timestamps would defeat that.
    for entry in bundle["rules"]:
        assert "id" not in entry
        assert "created_at" not in entry
        assert "last_evaluated_at" not in entry
        assert "last_match_count" not in entry
        # But the operational shape IS in the export.
        assert {"name", "description", "enabled", "priority", "definition"} <= set(
            entry
        )


@pytest.mark.asyncio
async def test_export_is_non_admin_readable(client: AsyncClient) -> None:
    """Backup / replication doesn't need admin — only writes do."""
    admin_headers = await _admin_headers(client)
    await client.post("/api/v1/rules", headers=admin_headers, json=_rule_body())

    user_headers = await _non_admin_headers(client)
    response = await client.get(
        "/api/v1/rules/bundle/export", headers=user_headers
    )
    assert response.status_code == 200


# ── Import ────────────────────────────────────────────────────


def _bundle(entries: list[dict]) -> dict:
    return {
        "version": "1",
        "exported_at": "2026-05-12T00:00:00Z",
        "rules": entries,
    }


@pytest.mark.asyncio
async def test_import_creates_new_rules(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    bundle = _bundle([_rule_body("Imported A"), _rule_body("Imported B")])

    response = await client.post(
        "/api/v1/rules/bundle/import",
        headers=headers,
        json={"bundle": bundle, "on_conflict": "rename"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] == 2
    assert body["renamed"] == 0
    assert body["skipped"] == 0
    assert body["overwritten"] == 0
    assert body["errors"] == 0
    assert {o["final_name"] for o in body["outcomes"]} == {
        "Imported A",
        "Imported B",
    }


@pytest.mark.asyncio
async def test_import_skip_strategy_preserves_existing(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    await client.post(
        "/api/v1/rules",
        headers=headers,
        json={**_rule_body("Conflict"), "priority": 500},
    )

    # Same name, different priority — under skip, the existing 500
    # must remain (not silently overwritten by 100).
    bundle = _bundle([_rule_body("Conflict")])
    response = await client.post(
        "/api/v1/rules/bundle/import",
        headers=headers,
        json={"bundle": bundle, "on_conflict": "skip"},
    )
    assert response.status_code == 200
    assert response.json()["skipped"] == 1

    rules = await client.get("/api/v1/rules", headers=headers)
    only = rules.json()
    assert len(only) == 1
    assert only[0]["priority"] == 500


@pytest.mark.asyncio
async def test_import_rename_strategy_creates_with_suffix(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    await client.post("/api/v1/rules", headers=headers, json=_rule_body("Dup"))

    bundle = _bundle([_rule_body("Dup")])
    response = await client.post(
        "/api/v1/rules/bundle/import",
        headers=headers,
        json={"bundle": bundle, "on_conflict": "rename"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["renamed"] == 1
    assert body["outcomes"][0]["final_name"] == "Dup (imported)"

    listing = await client.get("/api/v1/rules", headers=headers)
    assert {r["name"] for r in listing.json()} == {"Dup", "Dup (imported)"}


@pytest.mark.asyncio
async def test_import_overwrite_strategy_replaces_existing(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    create = await client.post(
        "/api/v1/rules",
        headers=headers,
        json={**_rule_body("Replaceable"), "priority": 50},
    )
    original_id = create.json()["id"]

    new_priority = 999
    bundle = _bundle([{**_rule_body("Replaceable"), "priority": new_priority}])
    response = await client.post(
        "/api/v1/rules/bundle/import",
        headers=headers,
        json={"bundle": bundle, "on_conflict": "overwrite"},
    )
    assert response.status_code == 200
    assert response.json()["overwritten"] == 1
    # Same id — the rule was mutated in place, not recreated. This
    # matters because rule_id is foreign-keyed by rule_evaluations,
    # and "overwrite means delete+create" would orphan the history.
    fetched = await client.get(
        f"/api/v1/rules/{original_id}", headers=headers
    )
    assert fetched.status_code == 200
    assert fetched.json()["priority"] == new_priority


@pytest.mark.asyncio
async def test_import_repeats_within_bundle_get_renamed(
    client: AsyncClient,
) -> None:
    """A single bundle with the same name twice shouldn't crash on
    the unique constraint — minted-this-batch collisions get a
    rename even under skip/overwrite, because the operator likely
    intended both entries to land."""
    headers = await _admin_headers(client)
    bundle = _bundle([_rule_body("Twins"), _rule_body("Twins")])
    response = await client.post(
        "/api/v1/rules/bundle/import",
        headers=headers,
        json={"bundle": bundle, "on_conflict": "rename"},
    )
    assert response.status_code == 200
    body = response.json()
    final_names = sorted(o["final_name"] for o in body["outcomes"])
    assert final_names == ["Twins", "Twins (imported)"]


@pytest.mark.asyncio
async def test_import_reports_invalid_entries_without_failing_batch(
    client: AsyncClient,
) -> None:
    """A bundle with a mix of good and bad entries imports the good
    ones and reports the bad ones as ``error`` outcomes."""
    headers = await _admin_headers(client)
    bad = {
        "name": "Broken",
        "description": "Bad definition",
        "priority": 100,
        "enabled": True,
        # ``set_severity`` requires ``severity``, not ``value``.
        "definition": {
            "match": {
                "all": [{"field": "category", "op": "eq", "value": "media"}]
            },
            "actions": [{"type": "set_severity", "value": "info"}],
        },
    }
    bundle = _bundle([_rule_body("Good one"), bad])

    response = await client.post(
        "/api/v1/rules/bundle/import",
        headers=headers,
        json={"bundle": bundle, "on_conflict": "rename"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1
    assert body["errors"] == 1
    error_outcome = next(o for o in body["outcomes"] if o["action"] == "error")
    assert error_outcome["name"] == "Broken"
    assert error_outcome["error"]  # non-empty error message


@pytest.mark.asyncio
async def test_import_rejects_unknown_bundle_version(client: AsyncClient) -> None:
    headers = await _admin_headers(client)
    bundle = _bundle([_rule_body()])
    bundle["version"] = "99"
    response = await client.post(
        "/api/v1/rules/bundle/import",
        headers=headers,
        json={"bundle": bundle, "on_conflict": "rename"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_import_admin_only(client: AsyncClient) -> None:
    admin_headers = await _admin_headers(client)
    # Ensure target user exists
    user_headers = await _non_admin_headers(client)
    bundle = _bundle([_rule_body()])
    response = await client.post(
        "/api/v1/rules/bundle/import",
        headers=user_headers,
        json={"bundle": bundle, "on_conflict": "rename"},
    )
    assert response.status_code == 403

    # And admin still works.
    response_admin = await client.post(
        "/api/v1/rules/bundle/import",
        headers=admin_headers,
        json={"bundle": bundle, "on_conflict": "rename"},
    )
    assert response_admin.status_code == 200


@pytest.mark.asyncio
async def test_import_round_trip_preserves_definitions(
    client: AsyncClient,
) -> None:
    """Export then import the same data → identical rule set. This
    is the property the export/import pair is supposed to preserve:
    two instances with the same export land identical rules."""
    headers = await _admin_headers(client)

    # Create three rules with different shapes.
    await client.post(
        "/api/v1/rules",
        headers=headers,
        json=_rule_body("Round A"),
    )
    await client.post(
        "/api/v1/rules",
        headers=headers,
        json={**_rule_body("Round B"), "enabled": False, "priority": 50},
    )
    await client.post(
        "/api/v1/rules",
        headers=headers,
        json={
            **_rule_body("Round C"),
            "definition": {
                "match": {
                    "any": [
                        {"field": "category", "op": "eq", "value": "media"},
                        {"field": "category", "op": "eq", "value": "subtitle"},
                    ]
                },
                "actions": [{"type": "add_tag", "tag": "round-c"}],
            },
        },
    )

    export = await client.get("/api/v1/rules/bundle/export", headers=headers)
    bundle = export.json()
    # Reset to a clean slate by deleting them all.
    listing = await client.get("/api/v1/rules", headers=headers)
    for r in listing.json():
        await client.delete(f"/api/v1/rules/{r['id']}", headers=headers)

    # Re-import.
    response = await client.post(
        "/api/v1/rules/bundle/import",
        headers=headers,
        json={"bundle": bundle, "on_conflict": "rename"},
    )
    assert response.status_code == 200
    assert response.json()["created"] == 3

    after = await client.get("/api/v1/rules", headers=headers)
    by_name = {r["name"]: r for r in after.json()}
    assert set(by_name) == {"Round A", "Round B", "Round C"}
    assert by_name["Round B"]["enabled"] is False
    assert by_name["Round B"]["priority"] == 50
    assert by_name["Round C"]["definition"]["match"]["any"][0]["value"] == "media"
