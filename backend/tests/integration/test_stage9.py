"""Stage 9 (audit follow-up) — quarantine + delete actions + extension rules.

Three behavioural surfaces pinned:

  1. Rule actions: ``Quarantine`` sets ``quarantined=True`` + emits;
     ``Delete(confirm=False)`` soft-deletes (quarantine + flag);
     ``Delete(confirm=True)`` moves the file to ``data_dir/trash/``
     and removes the row.
  2. ``/api/v1/system/extension-rules`` CRUD (admin-gated; conflict
     detection on unique extension).
  3. Scanner honours the four dispositions: ``ignore`` skips the
     file entirely; ``malicious`` sets severity=crit + quarantined;
     ``accepted`` caps severity at ok; ``stats_only`` indexes at
     severity=info.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.bus import EventBus, get_event_bus
from app.main import create_app
from app.models.extension_rule import MediaExtensionRule
from app.models.library import Library
from app.models.media import MediaFile
from app.models.rule import Rule
from app.models.user import User
from app.rules.evaluator import EvaluationInput, evaluate
from app.rules.schema import RuleDefinition
from app.services.media import FfprobeResult
from app.services.media.scanner import ScanOptions, Scanner
from app.services.repositories import (
    LibraryRepository,
    MediaRepository,
    RuleRepository,
)
from app.services.rules_service import RulesService
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

PASSWORD = "supersecret-password-1!"


# ── Stub ffprobe ────────────────────────────────────────────────
class StubFfprobe:
    @property
    def is_available(self) -> bool:
        return True

    async def probe(self, path: str) -> FfprobeResult:
        return FfprobeResult(ok=True, container="matroska", video_codec="h264")


# ── Pure-evaluator unit tests for the new actions ──────────────
def _input(path: str = "/lib/a.mkv") -> EvaluationInput:
    return EvaluationInput(
        media_file_id="mf-1",
        path=path,
        filename=Path(path).name,
        extension=Path(path).suffix.lstrip("."),
        category="media",
    )


def test_quarantine_action_sets_flag_and_reason() -> None:
    definition = RuleDefinition.model_validate(
        {
            "match": {
                "field": "extension",
                "op": "eq",
                "value": "mkv",
            },
            "actions": [
                {"type": "quarantine", "reason": "test-quarantine"},
            ],
        }
    )
    result = evaluate(definition, _input())
    assert result.matched is True
    assert result.quarantine is True
    assert result.quarantine_reason == "test-quarantine"
    assert result.delete_paths == []


def test_delete_confirm_false_falls_back_to_soft_delete() -> None:
    """Defensive default: without ``confirm=True`` the action
    quarantines + flags but does NOT enqueue a hard-delete path."""
    definition = RuleDefinition.model_validate(
        {
            "match": {"field": "extension", "op": "eq", "value": "mkv"},
            "actions": [{"type": "delete"}],
        }
    )
    result = evaluate(definition, _input())
    assert result.quarantine is True
    assert result.quarantine_reason == "Soft-delete via rule (confirm=false)"
    assert result.delete_paths == []


def test_delete_confirm_true_records_path_for_hard_delete() -> None:
    definition = RuleDefinition.model_validate(
        {
            "match": {"field": "extension", "op": "eq", "value": "mkv"},
            "actions": [{"type": "delete", "confirm": True}],
        }
    )
    result = evaluate(definition, _input("/lib/x.mkv"))
    assert result.quarantine is True
    assert result.delete_paths == ["/lib/x.mkv"]


def test_quarantine_merge_is_one_way_escalation() -> None:
    """Once any rule quarantines, the aggregate stays quarantined
    even if a later rule doesn't quarantine."""
    quarantining = evaluate(
        RuleDefinition.model_validate(
            {
                "match": {"field": "extension", "op": "eq", "value": "mkv"},
                "actions": [{"type": "quarantine", "reason": "first"}],
            }
        ),
        _input(),
    )
    benign = evaluate(
        RuleDefinition.model_validate(
            {
                "match": {"field": "extension", "op": "eq", "value": "mkv"},
                "actions": [{"type": "set_severity", "severity": "info"}],
            }
        ),
        _input(),
    )
    # Aggregate starts as benign, then merge quarantining in.
    benign.merge_into(quarantining)  # noqa: F841 (mutates quarantining)
    # ``merge_into`` mutates `other` — so look at quarantining (target).
    assert quarantining.quarantine is True
    assert quarantining.quarantine_reason == "first"


# ── Service-layer tests (filesystem effects) ───────────────────
@pytest_asyncio.fixture
async def session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncSession]:
    db_path = tmp_path / "stage9.db"
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
    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    sess = db._sessionmaker()  # type: ignore[misc]
    try:
        yield sess
    finally:
        await sess.close()
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_service_quarantine_action_sets_row_flag(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A rule with a quarantine action makes ``media_file.quarantined``
    transition False → True after evaluation."""
    library_root = tmp_path / "lib"
    library_root.mkdir()
    target = library_root / "junk.mkv"
    target.write_bytes(b"x" * 10)

    library = Library(name="movies", root_path=str(library_root), kind="movies")
    await LibraryRepository(session).add(library)
    media = MediaFile(
        library_id=library.id,
        path=str(target),
        filename="junk.mkv",
        extension="mkv",
        category="media",
        size_bytes=10,
        mtime=datetime(2026, 1, 1, tzinfo=UTC),
        relative_path="junk.mkv",
        severity="ok",
        severity_rank=0,
    )
    await MediaRepository(session).upsert_by_path(media)
    await session.commit()

    rule = Rule(
        name="quarantine-junk",
        description="",
        enabled=True,
        definition={
            "match": {"field": "extension", "op": "eq", "value": "mkv"},
            "actions": [{"type": "quarantine", "reason": "rule-driven"}],
        },
    )
    await RuleRepository(session).add(rule)
    await session.commit()

    bus = EventBus()
    bus.clear()
    quarantined_events: list[dict[str, object]] = []
    bus.subscribe(
        "media.quarantined",
        lambda e: quarantined_events.append(dict(getattr(e, "payload", {}))),
    )

    service = RulesService(session=session, event_bus=bus)
    await service.evaluate_library(library.id)

    await session.refresh(media)
    assert media.quarantined is True
    assert media.quarantined_reason == "rule-driven"
    assert len(quarantined_events) == 1
    assert quarantined_events[0]["id"] == media.id


@pytest.mark.asyncio
async def test_service_delete_confirm_false_soft_delete_only(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Without ``confirm=True``, the row stays + the file stays on
    disk; only the quarantine flag flips."""
    library_root = tmp_path / "lib"
    library_root.mkdir()
    target = library_root / "junk.mkv"
    target.write_bytes(b"x" * 10)

    library = Library(name="movies", root_path=str(library_root), kind="movies")
    await LibraryRepository(session).add(library)
    media = MediaFile(
        library_id=library.id,
        path=str(target),
        filename="junk.mkv",
        extension="mkv",
        category="media",
        size_bytes=10,
        mtime=datetime(2026, 1, 1, tzinfo=UTC),
        relative_path="junk.mkv",
        severity="ok",
        severity_rank=0,
    )
    await MediaRepository(session).upsert_by_path(media)
    await session.commit()

    rule = Rule(
        name="soft-delete-junk",
        description="",
        enabled=True,
        definition={
            "match": {"field": "extension", "op": "eq", "value": "mkv"},
            "actions": [{"type": "delete"}],  # confirm omitted → false
        },
    )
    await RuleRepository(session).add(rule)
    await session.commit()

    service = RulesService(session=session, event_bus=EventBus())
    await service.evaluate_library(library.id)

    # Row still exists, but quarantined.
    fresh = await MediaRepository(session).get(media.id)
    assert fresh is not None
    assert fresh.quarantined is True
    # File still on disk.
    assert target.exists()


@pytest.mark.asyncio
async def test_service_delete_confirm_true_hard_deletes_to_trash(
    session: AsyncSession, tmp_path: Path
) -> None:
    """With ``confirm=True`` the file is moved to ``data_dir/trash/``
    and the row is removed."""
    library_root = tmp_path / "lib"
    library_root.mkdir()
    target = library_root / "junk.mkv"
    target.write_bytes(b"x" * 10)

    library = Library(name="movies", root_path=str(library_root), kind="movies")
    await LibraryRepository(session).add(library)
    media = MediaFile(
        library_id=library.id,
        path=str(target),
        filename="junk.mkv",
        extension="mkv",
        category="media",
        size_bytes=10,
        mtime=datetime(2026, 1, 1, tzinfo=UTC),
        relative_path="junk.mkv",
        severity="ok",
        severity_rank=0,
    )
    await MediaRepository(session).upsert_by_path(media)
    await session.commit()
    media_id = media.id

    rule = Rule(
        name="hard-delete-junk",
        description="",
        enabled=True,
        definition={
            "match": {"field": "extension", "op": "eq", "value": "mkv"},
            "actions": [{"type": "delete", "confirm": True}],
        },
    )
    await RuleRepository(session).add(rule)
    await session.commit()

    service = RulesService(session=session, event_bus=EventBus())
    await service.evaluate_library(library.id)

    # Source file is gone from the library.
    assert not target.exists()
    # File is in the trash directory under data_dir.
    from app.core.settings import get_settings

    trash_root = Path(get_settings().data_dir) / "trash"
    assert trash_root.exists()
    moved = list(trash_root.glob(f"{media_id}__*"))
    assert len(moved) == 1, f"expected one trashed file, got {moved}"
    # Row is gone from the DB.
    fresh = await MediaRepository(session).get(media_id)
    assert fresh is None


# ── Scanner honours extension dispositions ─────────────────────
async def _seed_extension_rules(
    session: AsyncSession, rules: list[tuple[str, str]]
) -> None:
    for ext, disposition in rules:
        session.add(
            MediaExtensionRule(extension=ext, disposition=disposition, enabled=True)
        )
    await session.commit()


@pytest.mark.asyncio
async def test_scanner_ignore_disposition_skips_file(
    session: AsyncSession, tmp_path: Path
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    (library_root / "keep.mkv").write_bytes(b"x" * 10)
    (library_root / "drop.junk").write_bytes(b"x" * 10)

    library = Library(name="movies", root_path=str(library_root), kind="movies")
    await LibraryRepository(session).add(library)
    await session.commit()
    await _seed_extension_rules(session, [("junk", "ignore")])

    scanner = Scanner(
        session=session,
        event_bus=EventBus(),
        ffprobe=StubFfprobe(),  # type: ignore[arg-type]
    )
    await scanner.scan(library, options=ScanOptions(mode="full", run_rules=False))

    # Only ``keep.mkv`` was indexed; ``drop.junk`` was skipped.
    page = await MediaRepository(session).list(filt=None, offset=0, limit=20)
    paths = {m.path for m in page.items}
    assert paths == {str(library_root / "keep.mkv")}


@pytest.mark.asyncio
async def test_scanner_malicious_disposition_quarantines(
    session: AsyncSession, tmp_path: Path
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    (library_root / "danger.exe").write_bytes(b"x" * 10)

    library = Library(name="movies", root_path=str(library_root), kind="movies")
    await LibraryRepository(session).add(library)
    await session.commit()
    await _seed_extension_rules(session, [("exe", "malicious")])

    scanner = Scanner(
        session=session,
        event_bus=EventBus(),
        ffprobe=StubFfprobe(),  # type: ignore[arg-type]
    )
    await scanner.scan(library, options=ScanOptions(mode="full", run_rules=False))

    page = await MediaRepository(session).list(filt=None, offset=0, limit=20)
    assert len(page.items) == 1
    mf = page.items[0]
    assert mf.severity == "crit"
    assert mf.quarantined is True
    assert "malicious" in (mf.quarantined_reason or "")


@pytest.mark.asyncio
async def test_scanner_accepted_disposition_caps_severity(
    session: AsyncSession, tmp_path: Path
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    (library_root / "fine.txt").write_bytes(b"x" * 10)

    library = Library(name="movies", root_path=str(library_root), kind="movies")
    await LibraryRepository(session).add(library)
    await session.commit()
    await _seed_extension_rules(session, [("txt", "accepted")])

    scanner = Scanner(
        session=session,
        event_bus=EventBus(),
        ffprobe=StubFfprobe(),  # type: ignore[arg-type]
    )
    await scanner.scan(library, options=ScanOptions(mode="full", run_rules=False))

    page = await MediaRepository(session).list(filt=None, offset=0, limit=20)
    assert len(page.items) == 1
    assert page.items[0].severity == "ok"


@pytest.mark.asyncio
async def test_scanner_stats_only_indexes_at_info(
    session: AsyncSession, tmp_path: Path
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    (library_root / "side.nfo").write_bytes(b"<nfo/>")

    library = Library(name="movies", root_path=str(library_root), kind="movies")
    await LibraryRepository(session).add(library)
    await session.commit()
    await _seed_extension_rules(session, [("nfo", "stats_only")])

    scanner = Scanner(
        session=session,
        event_bus=EventBus(),
        ffprobe=StubFfprobe(),  # type: ignore[arg-type]
    )
    await scanner.scan(library, options=ScanOptions(mode="full", run_rules=False))

    page = await MediaRepository(session).list(filt=None, offset=0, limit=20)
    assert len(page.items) == 1
    # The row is indexed but flagged as info — visible in stats but
    # not escalated to warn/high.
    assert page.items[0].severity == "info"


# ── Extension rules API (admin gating + CRUD) ──────────────────
@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "stage9_api.db"
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


async def _admin_headers(client: AsyncClient) -> dict[str, str]:
    r = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "admin@example.com",
            "username": "admin",
            "password": PASSWORD,
        },
    )
    user = r.json()
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
async def test_extension_rule_crud_round_trip(client: AsyncClient) -> None:
    headers = await _admin_headers(client)

    create = await client.post(
        "/api/v1/system/extension-rules",
        headers=headers,
        json={"extension": ".MP4", "disposition": "accepted"},
    )
    assert create.status_code == 201, create.text
    body = create.json()
    # Extension is normalized (lowercase, no leading dot).
    assert body["extension"] == "mp4"
    assert body["disposition"] == "accepted"
    rule_id = body["id"]

    # PATCH flips disposition.
    patch = await client.patch(
        f"/api/v1/system/extension-rules/{rule_id}",
        headers=headers,
        json={"disposition": "malicious"},
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["disposition"] == "malicious"

    # LIST shows the row.
    listing = await client.get(
        "/api/v1/system/extension-rules", headers=headers
    )
    assert listing.status_code == 200
    rows = listing.json()
    assert any(r["id"] == rule_id for r in rows)

    # DELETE removes it.
    d = await client.delete(
        f"/api/v1/system/extension-rules/{rule_id}", headers=headers
    )
    assert d.status_code == 204
    listing2 = await client.get(
        "/api/v1/system/extension-rules", headers=headers
    )
    assert listing2.json() == []


@pytest.mark.asyncio
async def test_extension_rule_duplicate_extension_409(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    first = await client.post(
        "/api/v1/system/extension-rules",
        headers=headers,
        json={"extension": "mkv", "disposition": "accepted"},
    )
    assert first.status_code == 201
    second = await client.post(
        "/api/v1/system/extension-rules",
        headers=headers,
        json={"extension": "mkv", "disposition": "malicious"},
    )
    assert second.status_code == 409, second.text


@pytest.mark.asyncio
async def test_extension_rule_invalid_disposition_rejected(
    client: AsyncClient,
) -> None:
    headers = await _admin_headers(client)
    r = await client.post(
        "/api/v1/system/extension-rules",
        headers=headers,
        json={"extension": "txt", "disposition": "not-a-disposition"},
    )
    # Pydantic validator → 422.
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_extension_rule_create_requires_admin(
    client: AsyncClient,
) -> None:
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
    headers = {"authorization": f"Bearer {login.json()['access_token']}"}
    r = await client.post(
        "/api/v1/system/extension-rules",
        headers=headers,
        json={"extension": "mp4", "disposition": "accepted"},
    )
    assert r.status_code in (401, 403)
