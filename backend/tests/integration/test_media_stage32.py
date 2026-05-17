"""Stage 3 (audit follow-up) — Media API query-param contracts.

Pins three additions to ``GET /api/v1/media``:

  - ``scope=media`` / ``scope=non-media`` / ``scope=all`` tri-state
  - ``severities_empty=true`` sentinel returns a clean zero-row page
  - ``include_matched_rules=true`` attaches a chip-list per row

The repository-level contracts are covered in
``tests/unit/test_media_repo_sort.py``; this file verifies the API
layer correctly forwards the new query params into the filter.
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
from app.models.rule import Rule
from app.models.rule_evaluation import RuleEvaluation
from app.models.user import User
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def media_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "media_scope.db"
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
    assert response.status_code == 201, response.text
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
    assert login.status_code == 200
    return {"authorization": f"Bearer {login.json()['access_token']}"}


def _utcnow() -> _dt.datetime:
    return _dt.datetime(2026, 5, 14, tzinfo=_dt.UTC)


async def _seed_files() -> str:
    """Seed three rows: two media + one junk. Returns the library id."""
    async with get_database().session() as sess:
        library = Library(
            id="lib-1",
            name="Test",
            root_path="/srv/media",
            kind="movies",
            enabled=True,
        )
        sess.add(library)
        await sess.flush()

        sess.add_all(
            [
                MediaFile(
                    id="m1",
                    library_id="lib-1",
                    path="/srv/media/m1.mkv",
                    relative_path="m1.mkv",
                    filename="m1.mkv",
                    extension=".mkv",
                    size_bytes=1000,
                    mtime=_utcnow(),
                    category="media",
                    severity="warn",
                    severity_rank=30,
                    video_codec="hevc",
                    container="matroska",
                ),
                MediaFile(
                    id="m2",
                    library_id="lib-1",
                    path="/srv/media/m2.mkv",
                    relative_path="m2.mkv",
                    filename="m2.mkv",
                    extension=".mkv",
                    size_bytes=2000,
                    mtime=_utcnow(),
                    category="media",
                    severity="ok",
                    severity_rank=10,
                    video_codec="h264",
                    container="matroska",
                ),
                MediaFile(
                    id="j1",
                    library_id="lib-1",
                    path="/srv/media/j1.nfo",
                    relative_path="j1.nfo",
                    filename="j1.nfo",
                    extension=".nfo",
                    size_bytes=50,
                    mtime=_utcnow(),
                    category="junk",
                    severity="info",
                    severity_rank=15,
                ),
            ]
        )
        await sess.commit()
        return "lib-1"


# ── scope tri-state ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_scope_media_returns_only_media_rows(
    media_client: AsyncClient,
) -> None:
    headers = await _admin_headers(media_client)
    await _seed_files()

    r = await media_client.get(
        "/api/v1/media?scope=media", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2
    assert {item["id"] for item in body["items"]} == {"m1", "m2"}


@pytest.mark.asyncio
async def test_scope_non_media_excludes_media(
    media_client: AsyncClient,
) -> None:
    headers = await _admin_headers(media_client)
    await _seed_files()

    r = await media_client.get(
        "/api/v1/media?scope=non-media", headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == "j1"


@pytest.mark.asyncio
async def test_scope_all_is_equivalent_to_no_filter(
    media_client: AsyncClient,
) -> None:
    headers = await _admin_headers(media_client)
    await _seed_files()

    r = await media_client.get("/api/v1/media?scope=all", headers=headers)
    assert r.status_code == 200
    assert r.json()["total"] == 3


@pytest.mark.asyncio
async def test_scope_rejects_unknown_value(
    media_client: AsyncClient,
) -> None:
    """The endpoint pins ``scope`` to a regex; bogus values 422."""
    headers = await _admin_headers(media_client)
    await _seed_files()

    r = await media_client.get(
        "/api/v1/media?scope=bogus", headers=headers
    )
    assert r.status_code == 422


# ── empty-severities sentinel ────────────────────────────────────
@pytest.mark.asyncio
async def test_severities_empty_true_returns_zero_rows(
    media_client: AsyncClient,
) -> None:
    """The "hide all severities" UI sends this; the API must return a
    clean zero-row page rather than falling through to "no filter ⇒
    every row"."""
    headers = await _admin_headers(media_client)
    await _seed_files()

    r = await media_client.get(
        "/api/v1/media?severities_empty=true", headers=headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []


@pytest.mark.asyncio
async def test_severities_empty_absent_is_no_filter(
    media_client: AsyncClient,
) -> None:
    """Sanity: without the sentinel the endpoint still returns
    the full set. (Pre-Stage-05 this comment also mentioned a
    "default quarantine exclusion" — Stage 05 retired that
    workflow, so the set is now genuinely complete.)"""
    headers = await _admin_headers(media_client)
    await _seed_files()

    r = await media_client.get("/api/v1/media", headers=headers)
    assert r.status_code == 200
    assert r.json()["total"] == 3


# ── matched_rules attachment ─────────────────────────────────────
async def _seed_rule_evaluation() -> None:
    """Add a rule and a matching evaluation against file ``m1``."""
    async with get_database().session() as sess:
        sess.add(
            Rule(
                id="r1",
                name="HEVC media",
                enabled=True,
                priority=100,
                definition={},
                is_builtin=False,
            )
        )
        await sess.flush()
        sess.add(
            RuleEvaluation(
                id="ev1",
                media_file_id="m1",
                rule_id="r1",
                severity="warn",
                severity_rank=30,
                actions_summary={},
                evaluated_at=_utcnow(),
            )
        )
        await sess.commit()


@pytest.mark.asyncio
async def test_matched_rules_absent_by_default(
    media_client: AsyncClient,
) -> None:
    """Without the toggle, the per-row ``matched_rules`` is an empty
    list — the join didn't run."""
    headers = await _admin_headers(media_client)
    await _seed_files()
    await _seed_rule_evaluation()

    r = await media_client.get(
        "/api/v1/media?library_id=lib-1", headers=headers
    )
    assert r.status_code == 200
    body = r.json()
    for item in body["items"]:
        assert item.get("matched_rules", []) == []


@pytest.mark.asyncio
async def test_matched_rules_attached_when_toggle_on(
    media_client: AsyncClient,
) -> None:
    """With the toggle, the row gets the chip data."""
    headers = await _admin_headers(media_client)
    await _seed_files()
    await _seed_rule_evaluation()

    r = await media_client.get(
        "/api/v1/media?library_id=lib-1&include_matched_rules=true",
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    by_id = {item["id"]: item for item in body["items"]}
    # m1 has the match.
    assert by_id["m1"]["matched_rules"] == [
        {"rule_id": "r1", "rule_name": "HEVC media", "severity": "warn"}
    ]
    # m2 and j1 have no matches → empty list (NOT absent).
    assert by_id["m2"]["matched_rules"] == []
    assert by_id["j1"]["matched_rules"] == []


# ── sort against new whitelist keys ──────────────────────────────
@pytest.mark.asyncio
async def test_sort_by_video_codec_via_api(
    media_client: AsyncClient,
) -> None:
    """The column header on the Files page sends ``sort=video_codec``.
    Pre-Stage-3, the backend would silently fall back to the default
    severity order. Post-Stage-3, the codec column is in the
    whitelist and the sort takes effect."""
    headers = await _admin_headers(media_client)
    await _seed_files()

    r = await media_client.get(
        "/api/v1/media?scope=media&sort=video_codec&sort_dir=asc",
        headers=headers,
    )
    assert r.status_code == 200
    codecs = [item["video_codec"] for item in r.json()["items"]]
    assert codecs == ["h264", "hevc"]


@pytest.mark.asyncio
async def test_sort_by_severity_alias_via_api(
    media_client: AsyncClient,
) -> None:
    """``sort=severity`` is the alias the column header sends; the
    server internally sorts by severity_rank, so the result is
    semantically ordered (crit > error > high > warn > info > ok)
    not alphabetic."""
    headers = await _admin_headers(media_client)
    await _seed_files()

    r = await media_client.get(
        "/api/v1/media?sort=severity&sort_dir=desc", headers=headers
    )
    assert r.status_code == 200
    ranks = [item["severity_rank"] for item in r.json()["items"]]
    # Descending by rank → 30 (warn), 15 (info), 10 (ok).
    assert ranks == [30, 15, 10]
