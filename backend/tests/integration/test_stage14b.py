"""Stage 14b (audit follow-up) — per-rule matched-files endpoint.

Pins:
  1. ``GET /rules/{rule_id}/matched-files`` returns rows joined to
     media_files with path + filename + severity.
  2. 404 for unknown rule id; empty array for known-but-unmatched.
  3. Ordering is severity_rank desc then evaluated_at desc.
  4. Limit param honored.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.events.bus import get_event_bus
from app.main import create_app
from app.models.library import Library
from app.models.media import MediaFile
from app.models.rule import Rule
from app.models.rule_evaluation import RuleEvaluation
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "stage14b.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("AUDITARR_DATA_DIR", str(data_dir))
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


async def _headers(client: AsyncClient) -> dict[str, str]:
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
    return {"authorization": f"Bearer {login.json()['access_token']}"}


async def _seed_rule_with_matches() -> str:
    """Seed: 1 library, 3 media files, 1 rule, 3 evaluations at
    different severity ranks. Returns the rule id."""
    now = datetime.now(UTC)
    async with get_database().session() as sess:
        sess.add(
            Library(
                id="lib-1",
                name="Movies",
                root_path="/tmp/lib",
                kind="movies",
                enabled=True,
            )
        )
        for i, sev in enumerate(("info", "warn", "error")):
            sess.add(
                MediaFile(
                    id=f"mf-{i}",
                    library_id="lib-1",
                    path=f"/tmp/lib/m{i}.mkv",
                    filename=f"m{i}.mkv",
                    relative_path=f"m{i}.mkv",
                    extension="mkv",
                    category="media",
                    size_bytes=10,
                    mtime=now,
                    severity=sev,
                    severity_rank={"info": 10, "warn": 30, "error": 50}[sev],
                )
            )
        sess.add(
            Rule(
                id="rule-1",
                name="Matchy rule",
                description="",
                enabled=True,
                is_builtin=False,
                priority=100,
                definition={"all": []},
            )
        )
        for i, (sev, rank) in enumerate(
            (("info", 10), ("error", 50), ("warn", 30))
        ):
            sess.add(
                RuleEvaluation(
                    media_file_id=f"mf-{i}",
                    rule_id="rule-1",
                    severity=sev,
                    severity_rank=rank,
                    actions_summary={},
                    evaluated_at=now - timedelta(minutes=i),
                )
            )
        await sess.commit()
    return "rule-1"


@pytest.mark.asyncio
async def test_matched_files_returns_joined_rows(
    client: AsyncClient,
) -> None:
    headers = await _headers(client)
    rule_id = await _seed_rule_with_matches()

    r = await client.get(
        f"/api/v1/rules/{rule_id}/matched-files", headers=headers
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 3
    # Every row has joined file fields.
    for row in rows:
        assert "media_file_id" in row
        assert "library_id" in row
        assert "path" in row
        assert "filename" in row
        assert row["library_id"] == "lib-1"
        assert row["path"].startswith("/tmp/lib/")
        assert row["filename"].endswith(".mkv")


@pytest.mark.asyncio
async def test_matched_files_orders_by_severity_then_time(
    client: AsyncClient,
) -> None:
    """severity_rank desc primary, evaluated_at desc secondary."""
    headers = await _headers(client)
    rule_id = await _seed_rule_with_matches()

    r = await client.get(
        f"/api/v1/rules/{rule_id}/matched-files", headers=headers
    )
    rows = r.json()
    # Severity ranks: 50, 30, 10.
    assert [row["severity_rank"] for row in rows] == [50, 30, 10]
    # Matching severities.
    assert [row["severity"] for row in rows] == ["error", "warn", "info"]


@pytest.mark.asyncio
async def test_matched_files_404_for_unknown_rule(
    client: AsyncClient,
) -> None:
    headers = await _headers(client)
    r = await client.get(
        "/api/v1/rules/does-not-exist/matched-files", headers=headers
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_matched_files_empty_for_unmatched_rule(
    client: AsyncClient,
) -> None:
    """A rule that exists but has no evaluations returns an empty
    array, NOT 404. The UI distinguishes the two states."""
    headers = await _headers(client)
    async with get_database().session() as sess:
        sess.add(
            Rule(
                id="rule-empty",
                name="Empty rule",
                description="",
                enabled=True,
                is_builtin=False,
                priority=50,
                definition={"all": []},
            )
        )
        await sess.commit()

    r = await client.get(
        "/api/v1/rules/rule-empty/matched-files", headers=headers
    )
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.asyncio
async def test_matched_files_limit_honored(client: AsyncClient) -> None:
    headers = await _headers(client)
    rule_id = await _seed_rule_with_matches()

    r = await client.get(
        f"/api/v1/rules/{rule_id}/matched-files?limit=2", headers=headers
    )
    assert r.status_code == 200
    assert len(r.json()) == 2
