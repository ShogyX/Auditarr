"""Stage 18 (audit follow-up) — tag-scope filter on media + rule
evaluation, tag catalog endpoint, tag schema hint on automation.

Pins:
  1. ``MediaFilter.tags_any`` returns only files carrying at least
     one of the listed tags.
  2. The filter is OR-semantic (any-of), not AND.
  3. ``tags_any=[]`` and ``tags_any=None`` both behave as "no filter"
     (no surprise zero-row collapse).
  4. ``RulesService.evaluate_library(tags_any=...)`` scopes
     re-evaluation to tagged files.
  5. ``_run_evaluate_library`` honors the ``tags`` arg.
  6. ``GET /tags`` returns the union of distinct tag names sorted.
  7. ``GET /automation/jobs`` surfaces the new tag schema field on
     ``evaluate_library`` with ``format: "tag_list"``.
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
from app.models.library import Library
from app.models.media import MediaFile
from app.models.tag import MediaTag
from app.models.user import User
from app.security.secrets import reset_secret_box
from app.services.repositories.media import MediaFilter, MediaRepository
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "stage18.db"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("AUDITARR_DATA_DIR", str(data_dir))
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    from app.core.settings import get_settings

    get_settings.cache_clear()
    reset_secret_box()

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


async def _admin(client: AsyncClient) -> dict[str, str]:
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


async def _seed_files_with_tags() -> tuple[str, str, str]:
    """Seed a library and three files with overlapping tag sets.
    Returns ``(file_a_id, file_b_id, file_c_id)``.

    Tag layout:
      A → ["sonarr", "4K"]
      B → ["sonarr"]
      C → ["radarr"]
    """
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    async with get_database().session() as sess:
        lib = Library(name="Lib", root_path="/data", kind="movies")
        sess.add(lib)
        await sess.flush()
        files = []
        for label in ("a", "b", "c"):
            mf = MediaFile(
                library_id=lib.id,
                path=f"/data/{label}.mkv",
                relative_path=f"{label}.mkv",
                filename=f"{label}.mkv",
                extension="mkv",
                size_bytes=1024,
                mtime=now,
            )
            sess.add(mf)
            files.append(mf)
        await sess.flush()
        sess.add(MediaTag(media_file_id=files[0].id, name="sonarr", source="integration"))
        sess.add(MediaTag(media_file_id=files[0].id, name="4K", source="manual"))
        sess.add(MediaTag(media_file_id=files[1].id, name="sonarr", source="integration"))
        sess.add(MediaTag(media_file_id=files[2].id, name="radarr", source="integration"))
        await sess.commit()
        return files[0].id, files[1].id, files[2].id


# ── 1+2+3 — MediaFilter.tags_any ─────────────────────────────
@pytest.mark.asyncio
async def test_tags_any_matches_files_with_one_of_listed_tags(
    client: AsyncClient,
) -> None:
    await _admin(client)
    a, b, c = await _seed_files_with_tags()

    async with get_database().session() as sess:
        repo = MediaRepository(sess)
        page = await repo.list(filt=MediaFilter(tags_any=["sonarr"]), offset=0, limit=100)
        ids = {m.id for m in page.items}
        assert ids == {a, b}, f"expected A+B (sonarr), got {ids}"


@pytest.mark.asyncio
async def test_tags_any_is_or_semantic_not_and(
    client: AsyncClient,
) -> None:
    """A file tagged 'sonarr' OR '4K' surfaces under either query;
    OR-of-tags means tagging 'sonarr' alone is enough."""
    await _admin(client)
    a, b, c = await _seed_files_with_tags()

    async with get_database().session() as sess:
        repo = MediaRepository(sess)
        page = await repo.list(
            filt=MediaFilter(tags_any=["sonarr", "radarr"]),
            offset=0,
            limit=100,
        )
        ids = {m.id for m in page.items}
        # OR semantics: union of sonarr-tagged AND radarr-tagged.
        assert ids == {a, b, c}


@pytest.mark.asyncio
async def test_tags_any_empty_list_means_no_filter(
    client: AsyncClient,
) -> None:
    """Empty list must NOT collapse to "match nothing" — the
    automation form sends ``tags: []`` when the operator didn't pick
    any chips, and that should be equivalent to "no filter"."""
    await _admin(client)
    a, b, c = await _seed_files_with_tags()

    async with get_database().session() as sess:
        repo = MediaRepository(sess)
        page = await repo.list(filt=MediaFilter(tags_any=[]), offset=0, limit=100)
        ids = {m.id for m in page.items}
        assert ids == {a, b, c}


# ── 4+5 — evaluate_library + runner forward the arg ──────────
@pytest.mark.asyncio
async def test_evaluate_library_scopes_to_tagged_files(
    client: AsyncClient,
) -> None:
    """``RulesService.evaluate_library(tags_any=...)`` only walks
    files matching the tag scope. We can't easily count evaluations
    here without rules; instead verify the filter is passed through
    by inspecting the count returned (which is the number of files
    visited)."""
    from app.events.bus import get_event_bus
    from app.services.rules_service import RulesService

    await _admin(client)
    await _seed_files_with_tags()

    async with get_database().session() as sess:
        # Find the library id we seeded.
        from sqlalchemy import select

        library_id = (
            await sess.execute(select(Library.id))
        ).scalar_one()
        svc = RulesService(session=sess, event_bus=get_event_bus())
        # No rules registered → evaluate_files no-ops, but the
        # returned count is the visited-file count.
        scoped = await svc.evaluate_library(library_id, tags_any=["sonarr"])
        unscoped = await svc.evaluate_library(library_id)
        assert scoped == 2, f"expected 2 sonarr-tagged files, got {scoped}"
        assert unscoped == 3, f"expected all 3 files, got {unscoped}"


@pytest.mark.asyncio
async def test_run_evaluate_library_passes_tags_arg(
    client: AsyncClient,
) -> None:
    """The job runner honors ``args.tags`` and surfaces the scope in
    the response payload."""
    from app.automation.jobs import _run_evaluate_library
    from app.core.registry import get_registry
    from app.events.bus import get_event_bus
    from sqlalchemy import select

    await _admin(client)
    await _seed_files_with_tags()

    async with get_database().session() as sess:
        library_id = (
            await sess.execute(select(Library.id))
        ).scalar_one()
        ctx = {"bus": get_event_bus(), "registry": get_registry()}
        result = await _run_evaluate_library(
            sess,
            args={"library_id": library_id, "tags": ["sonarr"]},
            ctx=ctx,
        )
        assert result["files_evaluated"] == 2
        assert result["tags_any"] == ["sonarr"]


# ── 6 — GET /tags ────────────────────────────────────────────
@pytest.mark.asyncio
async def test_tags_catalog_returns_distinct_sorted_names(
    client: AsyncClient,
) -> None:
    headers = await _admin(client)
    await _seed_files_with_tags()

    r = await client.get("/api/v1/tags", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # Three distinct names — "4K", "radarr", "sonarr" — alpha-sorted.
    # Casing preserved per the Stage 13 guard rail.
    assert body == ["4K", "radarr", "sonarr"]


# ── 7 — automation /jobs surfaces tag_list format ────────────
@pytest.mark.asyncio
async def test_automation_jobs_surfaces_tag_list_format(
    client: AsyncClient,
) -> None:
    headers = await _admin(client)
    r = await client.get("/api/v1/automation/jobs", headers=headers)
    assert r.status_code == 200, r.text
    jobs = {j["key"]: j for j in r.json()}
    spec = jobs["evaluate_library"]["args_schema"]["properties"]["tags"]
    assert spec["type"] == "array"
    assert spec.get("format") == "tag_list"
    assert spec["items"]["type"] == "string"
