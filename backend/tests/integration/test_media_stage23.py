"""Stage 23 — Files page backend additions.

Covers the three additive backend surfaces this stage introduces:

* sortable column on ``GET /api/v1/media`` with whitelist enforcement,
* per-file rule-evaluation listing at ``GET /api/v1/media/{id}/evaluations``,
* bulk re-evaluation at ``POST /api/v1/media/bulk/reevaluate``.

We deliberately don't re-validate the underlying behavior of the scanner
or the rules engine here — those each have their own test suites
(``test_scanner.py``, ``test_rules_evaluator.py``, ``test_rules_api.py``).
This file pins only the contract changes.
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
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def media_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "media.db"
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


def _seed_library(root: Path) -> None:
    # 4 files of varying sizes — exercises sort by size_bytes meaningfully.
    sub = root / "Movies" / "Sample (2024)"
    sub.mkdir(parents=True)
    (sub / "movie.mkv").write_bytes(b"x" * 200)
    (sub / "movie.eng.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nhi\n"
    )
    (sub / "poster.jpg").write_bytes(b"\xff\xd8\xff")
    (sub / ".DS_Store").write_bytes(b"x")


async def _scan(client: AsyncClient, headers: dict[str, str], root: Path) -> str:
    create = await client.post(
        "/api/v1/libraries",
        headers=headers,
        json={"name": "Movies", "root_path": str(root), "kind": "movies"},
    )
    library_id = create.json()["id"]
    scan = await client.post(
        # Stage 8 (audit follow-up): tests seed via sync scan.
        f"/api/v1/scans/libraries/{library_id}?enqueue=false",
        headers=headers,
        json={"mode": "full"},
    )
    assert scan.status_code == 202, scan.text
    return library_id


# ── Sort ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_media_list_sort_by_size_asc(
    media_client: AsyncClient, tmp_path: Path
) -> None:
    headers = await _admin_headers(media_client)
    root = tmp_path / "lib"
    root.mkdir()
    _seed_library(root)
    await _scan(media_client, headers, root)

    response = await media_client.get(
        "/api/v1/media?sort=size_bytes&sort_dir=asc", headers=headers
    )
    assert response.status_code == 200
    sizes = [item["size_bytes"] for item in response.json()["items"]]
    assert sizes == sorted(sizes), f"expected ascending size order, got {sizes}"


@pytest.mark.asyncio
async def test_media_list_sort_by_filename_desc(
    media_client: AsyncClient, tmp_path: Path
) -> None:
    headers = await _admin_headers(media_client)
    root = tmp_path / "lib"
    root.mkdir()
    _seed_library(root)
    await _scan(media_client, headers, root)

    response = await media_client.get(
        "/api/v1/media?sort=filename&sort_dir=desc", headers=headers
    )
    assert response.status_code == 200
    names = [item["filename"] for item in response.json()["items"]]
    assert names == sorted(names, reverse=True), names


@pytest.mark.asyncio
async def test_media_list_sort_unknown_column_falls_back(
    media_client: AsyncClient, tmp_path: Path
) -> None:
    """An unrecognized sort column should NOT 422; the repository falls
    back to the legacy severity-first order. This is a deliberate
    forgiveness: the UI sometimes passes through a column key that
    isn't whitelisted (e.g. a JSON blob), and breaking the listing for
    that case would be worse than degrading to the default order.
    """
    headers = await _admin_headers(media_client)
    root = tmp_path / "lib"
    root.mkdir()
    _seed_library(root)
    await _scan(media_client, headers, root)

    response = await media_client.get(
        "/api/v1/media?sort=probe&sort_dir=desc", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["total"] == 4


@pytest.mark.asyncio
async def test_media_list_sort_dir_pattern_validation(
    media_client: AsyncClient,
) -> None:
    """``sort_dir`` is constrained to ``asc|desc`` at the API layer."""
    headers = await _admin_headers(media_client)
    response = await media_client.get(
        "/api/v1/media?sort=path&sort_dir=sideways", headers=headers
    )
    assert response.status_code == 422


# ── Per-file evaluations ──────────────────────────────────────────────


async def _create_rule(client: AsyncClient, headers: dict[str, str], name: str) -> str:
    # Build a trivial rule that matches every media file by category.
    body = {
        "name": name,
        "description": "Stage 23 fixture rule",
        "enabled": True,
        "priority": 10,
        "definition": {
            "match": {"all": [{"field": "category", "op": "eq", "value": "media"}]},
            "actions": [{"type": "set_severity", "severity": "info"}],
        },
    }
    response = await client.post("/api/v1/rules", headers=headers, json=body)
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


@pytest.mark.asyncio
async def test_media_evaluations_returns_rule_names(
    media_client: AsyncClient, tmp_path: Path
) -> None:
    headers = await _admin_headers(media_client)
    root = tmp_path / "lib"
    root.mkdir()
    _seed_library(root)
    library_id = await _scan(media_client, headers, root)
    rule_id = await _create_rule(media_client, headers, "info-on-media")

    # Trigger evaluation via the existing library-evaluate endpoint
    # (Stage 6) — we're only validating the per-file READ here, not
    # building a new write path.
    evaluate = await media_client.post(
        f"/api/v1/rules/libraries/{library_id}/evaluate", headers=headers
    )
    assert evaluate.status_code == 200, evaluate.text

    media = await media_client.get("/api/v1/media?category=media", headers=headers)
    media_file = media.json()["items"][0]
    media_id = media_file["id"]

    response = await media_client.get(
        f"/api/v1/media/{media_id}/evaluations", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["rule_id"] == rule_id
    assert body[0]["rule_name"] == "info-on-media"
    assert body[0]["rule_enabled"] is True
    assert body[0]["severity"] == "info"


@pytest.mark.asyncio
async def test_media_evaluations_404_for_unknown_file(
    media_client: AsyncClient,
) -> None:
    headers = await _admin_headers(media_client)
    response = await media_client.get(
        "/api/v1/media/does-not-exist/evaluations", headers=headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_media_evaluations_keeps_rows_for_disabled_rules(
    media_client: AsyncClient, tmp_path: Path
) -> None:
    """The evaluation log preserves historical state — disabling a rule
    later does NOT delete its evaluation rows. The endpoint surfaces
    those with ``rule_enabled: false`` so the drawer can show "this was
    flagged but the rule is no longer active"."""
    headers = await _admin_headers(media_client)
    root = tmp_path / "lib"
    root.mkdir()
    _seed_library(root)
    library_id = await _scan(media_client, headers, root)
    rule_id = await _create_rule(media_client, headers, "disabled-later")

    await media_client.post(
        f"/api/v1/rules/libraries/{library_id}/evaluate", headers=headers
    )

    # Disable the rule directly via DB (no UI endpoint disables a
    # single rule today; the rules API uses PATCH).
    async with get_database().session() as sess:
        await sess.execute(
            update(Rule).where(Rule.id == rule_id).values(enabled=False)
        )
        await sess.commit()

    media = await media_client.get("/api/v1/media?category=media", headers=headers)
    media_id = media.json()["items"][0]["id"]

    response = await media_client.get(
        f"/api/v1/media/{media_id}/evaluations", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["rule_enabled"] is False


# ── Bulk re-evaluate ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_reevaluate_updates_severity(
    media_client: AsyncClient, tmp_path: Path
) -> None:
    headers = await _admin_headers(media_client)
    root = tmp_path / "lib"
    root.mkdir()
    _seed_library(root)
    await _scan(media_client, headers, root)

    media = await media_client.get("/api/v1/media", headers=headers)
    ids = [item["id"] for item in media.json()["items"]]
    assert len(ids) == 4

    # No rules → no evaluations → severity stays at default 'ok' even
    # after bulk reevaluation. We're checking the contract here, not
    # the rules engine.
    response = await media_client.post(
        "/api/v1/media/bulk/reevaluate",
        headers=headers,
        json={"media_ids": ids},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["files_evaluated"] == 4
    assert body["files_not_found"] == []


@pytest.mark.asyncio
async def test_bulk_reevaluate_reports_unknown_ids(
    media_client: AsyncClient, tmp_path: Path
) -> None:
    headers = await _admin_headers(media_client)
    root = tmp_path / "lib"
    root.mkdir()
    _seed_library(root)
    await _scan(media_client, headers, root)

    media = await media_client.get("/api/v1/media", headers=headers)
    real_id = media.json()["items"][0]["id"]

    response = await media_client.post(
        "/api/v1/media/bulk/reevaluate",
        headers=headers,
        json={"media_ids": [real_id, "ghost-1", "ghost-2"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["files_evaluated"] == 1
    assert sorted(body["files_not_found"]) == ["ghost-1", "ghost-2"]


@pytest.mark.asyncio
async def test_bulk_reevaluate_rejects_duplicates(
    media_client: AsyncClient, tmp_path: Path
) -> None:
    headers = await _admin_headers(media_client)
    root = tmp_path / "lib"
    root.mkdir()
    _seed_library(root)
    await _scan(media_client, headers, root)

    media = await media_client.get("/api/v1/media", headers=headers)
    real_id = media.json()["items"][0]["id"]

    response = await media_client.post(
        "/api/v1/media/bulk/reevaluate",
        headers=headers,
        json={"media_ids": [real_id, real_id]},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_bulk_reevaluate_rejects_empty(media_client: AsyncClient) -> None:
    headers = await _admin_headers(media_client)
    response = await media_client.post(
        "/api/v1/media/bulk/reevaluate",
        headers=headers,
        json={"media_ids": []},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_bulk_reevaluate_rejects_oversized(media_client: AsyncClient) -> None:
    headers = await _admin_headers(media_client)
    response = await media_client.post(
        "/api/v1/media/bulk/reevaluate",
        headers=headers,
        json={"media_ids": [f"x{i}" for i in range(501)]},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_bulk_reevaluate_non_admin_forbidden(
    media_client: AsyncClient, tmp_path: Path
) -> None:
    """Admin-only — non-admins get 403 even with valid file IDs.

    The endpoint mutates ``rule_evaluations`` and the file's
    denormalized severity, matching the gate on
    ``POST /api/v1/rules/libraries/{library_id}/evaluate``.
    """
    admin_headers = await _admin_headers(media_client)
    root = tmp_path / "lib"
    root.mkdir()
    _seed_library(root)
    await _scan(media_client, admin_headers, root)

    user_headers = await _non_admin_headers(media_client)
    media = await media_client.get("/api/v1/media", headers=user_headers)
    real_id = media.json()["items"][0]["id"]

    response = await media_client.post(
        "/api/v1/media/bulk/reevaluate",
        headers=user_headers,
        json={"media_ids": [real_id]},
    )
    assert response.status_code == 403
