"""Stage 9 (audit follow-up) — delete actions + extension rules.

Stage 05 (v1.7) updated the surface this file tests. The Stage 9
contract originally covered ``Quarantine`` actions + a soft-delete
mode (``Delete(confirm=False)``); Stage 05 retired both
(Section A.0 — "delete means delete"). The file now pins the
Stage 05 contract:

  1. Rule actions: ``Delete`` is unconditional. It records the
     file's path + a reason in the evaluator result, and the
     service layer moves the file to ``data_dir/trash/``, drops
     the row, and writes a ``file.deleted`` audit-log entry.
     ``Quarantine`` action and the ``confirm`` flag are gone from
     the schema; this file's validation tests pin those rejections.
  2. ``/api/v1/system/extension-rules`` CRUD (admin-gated;
     conflict detection on unique extension) — unchanged.
  3. Scanner honours the four dispositions: ``ignore`` skips the
     file entirely; ``malicious`` sets severity=crit (no longer
     quarantines — that column is gone); ``accepted`` caps
     severity at ok; ``stats_only`` indexes at severity=info.
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


def test_delete_action_records_path_and_reason() -> None:
    """Stage 05 (v1.7) — a Delete action surfaces the file's path
    in ``delete_paths`` and the operator-supplied reason in
    ``delete_reasons``. The lists stay paired by index."""
    definition = RuleDefinition.model_validate(
        {
            "match": {
                "field": "extension",
                "op": "eq",
                "value": "mkv",
            },
            "actions": [
                {"type": "delete", "reason": "Plex incompat"},
            ],
            "acknowledged_destructive": True,
        }
    )
    result = evaluate(definition, _input("/lib/x.mkv"))
    assert result.matched is True
    assert result.delete_paths == ["/lib/x.mkv"]
    assert result.delete_reasons == ["Plex incompat"]


def test_delete_action_without_reason_synthesizes_one() -> None:
    """Stage 05 — when no ``reason`` is supplied, the evaluator
    fills in a generic "Deleted by rule" so the audit log entry
    is never blank."""
    definition = RuleDefinition.model_validate(
        {
            "match": {"field": "extension", "op": "eq", "value": "mkv"},
            "actions": [{"type": "delete"}],
            "acknowledged_destructive": True,
        }
    )
    result = evaluate(definition, _input("/lib/x.mkv"))
    assert result.delete_paths == ["/lib/x.mkv"]
    assert result.delete_reasons == ["Deleted by rule"]


def test_quarantine_action_is_rejected_at_validation() -> None:
    """Stage 05 (v1.7) — ``type: "quarantine"`` is no longer a
    valid action; ``RuleDefinition.model_validate`` rejects it.
    Operators who had quarantine actions stored have their bodies
    rewritten by the 0015 migration."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        RuleDefinition.model_validate(
            {
                "match": {"field": "extension", "op": "eq", "value": "mkv"},
                "actions": [{"type": "quarantine", "reason": "irrelevant"}],
            }
        )


def test_delete_action_rejects_confirm_flag() -> None:
    """Stage 05 — the pre-Stage-05 ``confirm`` flag on Delete is
    gone (delete is unconditional). With Pydantic ``extra="forbid"``,
    any persisted body still carrying ``confirm`` is rejected; the
    migration scrubs the flag before it gets here."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        RuleDefinition.model_validate(
            {
                "match": {"field": "extension", "op": "eq", "value": "mkv"},
                "actions": [{"type": "delete", "confirm": True}],
            }
        )


def test_delete_merge_concatenates_paths_and_reasons_pairwise() -> None:
    """Stage 05 — when ``merge_into`` reduces two evaluator
    results down, the (path, reason) pairs stay aligned by
    index so the service layer can pick any one of them and
    keep the audit entry coherent."""
    first = evaluate(
        RuleDefinition.model_validate(
            {
                "match": {"field": "extension", "op": "eq", "value": "mkv"},
                "actions": [{"type": "delete", "reason": "first"}],
                "acknowledged_destructive": True,
            }
        ),
        _input("/lib/a.mkv"),
    )
    second = evaluate(
        RuleDefinition.model_validate(
            {
                "match": {"field": "extension", "op": "eq", "value": "mkv"},
                "actions": [{"type": "delete", "reason": "second"}],
                "acknowledged_destructive": True,
            }
        ),
        _input("/lib/a.mkv"),
    )
    second.merge_into(first)
    # Two delete actions matched the same file — both paths and
    # both reasons are present, paired by position. The service
    # layer picks ``reasons[0]`` for the audit entry.
    assert first.delete_paths == ["/lib/a.mkv", "/lib/a.mkv"]
    assert first.delete_reasons == ["first", "second"]


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
async def test_service_delete_action_hard_deletes_to_trash_and_audit_logs(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Stage 05 (v1.7) — a rule with a Delete action moves the
    file to ``data_dir/trash/``, removes the row, and writes an
    audit log entry tagged ``file.deleted`` with the rule's
    reason in the metadata."""
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
        name="delete-junk",
        description="",
        enabled=True,
        definition={
            "match": {"field": "extension", "op": "eq", "value": "mkv"},
            "actions": [
                {"type": "delete", "reason": "junk extension"},
            ],
            "acknowledged_destructive": True,
        },
    )
    await RuleRepository(session).add(rule)
    await session.commit()

    bus = EventBus()
    bus.clear()
    deleted_events: list[dict[str, object]] = []
    bus.subscribe(
        "media.deleted",
        lambda e: deleted_events.append(dict(getattr(e, "payload", {}))),
    )

    service = RulesService(session=session, event_bus=bus)
    await service.evaluate_library(library.id)
    await session.commit()

    # Source is gone from the library.
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
    # Audit log carries the reason + path.
    from app.models.audit_log import AuditLogEntry
    from sqlalchemy import select

    rows = (await session.execute(select(AuditLogEntry))).scalars().all()
    delete_entries = [r for r in rows if r.action == "file.deleted"]
    assert len(delete_entries) == 1, (
        f"expected one file.deleted audit entry, got {len(delete_entries)}"
    )
    entry = delete_entries[0]
    assert entry.target_id == media_id
    assert entry.target_type == "media_file"
    assert entry.actor_label == "rules"
    md = entry.metadata_ or {}
    assert md.get("reason") == "junk extension"
    assert md.get("path") == str(target)
    # Bus event also fired with the reason.
    assert len(deleted_events) == 1
    assert deleted_events[0]["reason"] == "junk extension"


@pytest.mark.asyncio
async def test_service_delete_action_without_reason_synthesizes_audit_text(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Stage 05 — when a Delete action carries no reason, the
    audit entry still records a non-empty reason ("Deleted by
    rule") so the trail isn't blank."""
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
        name="delete-junk-no-reason",
        description="",
        enabled=True,
        definition={
            "match": {"field": "extension", "op": "eq", "value": "mkv"},
            "actions": [{"type": "delete"}],
            "acknowledged_destructive": True,
        },
    )
    await RuleRepository(session).add(rule)
    await session.commit()

    service = RulesService(session=session, event_bus=EventBus())
    await service.evaluate_library(library.id)
    await session.commit()

    from app.models.audit_log import AuditLogEntry
    from sqlalchemy import select

    rows = (await session.execute(select(AuditLogEntry))).scalars().all()
    delete_entries = [r for r in rows if r.action == "file.deleted"]
    assert len(delete_entries) == 1
    md = delete_entries[0].metadata_ or {}
    assert md.get("reason") == "Deleted by rule"


@pytest.mark.asyncio
async def test_service_delete_action_preserves_row_when_file_already_gone(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Stage 05 — if the source file has already vanished from
    disk (operator deleted it manually), the rule pipeline logs
    a warning but still proceeds to remove the row + audit-log,
    because the operator's intent ("don't track this file") is
    served either way. The trash-path field is None in that
    case so a forensic reader knows nothing was moved."""
    library_root = tmp_path / "lib"
    library_root.mkdir()
    # Deliberately do NOT create the file on disk.
    target_path = library_root / "ghost.mkv"

    library = Library(name="movies", root_path=str(library_root), kind="movies")
    await LibraryRepository(session).add(library)
    media = MediaFile(
        library_id=library.id,
        path=str(target_path),
        filename="ghost.mkv",
        extension="mkv",
        category="media",
        size_bytes=10,
        mtime=datetime(2026, 1, 1, tzinfo=UTC),
        relative_path="ghost.mkv",
        severity="ok",
        severity_rank=0,
    )
    await MediaRepository(session).upsert_by_path(media)
    await session.commit()
    media_id = media.id

    rule = Rule(
        name="delete-ghost",
        description="",
        enabled=True,
        definition={
            "match": {"field": "extension", "op": "eq", "value": "mkv"},
            "actions": [{"type": "delete", "reason": "ghost cleanup"}],
            "acknowledged_destructive": True,
        },
    )
    await RuleRepository(session).add(rule)
    await session.commit()

    service = RulesService(session=session, event_bus=EventBus())
    await service.evaluate_library(library.id)
    await session.commit()

    # Row is gone (clean delete in service).
    fresh = await MediaRepository(session).get(media_id)
    assert fresh is None
    # Audit entry still recorded; trash_path is None because the
    # file was never there to move.
    from app.models.audit_log import AuditLogEntry
    from sqlalchemy import select

    rows = (await session.execute(select(AuditLogEntry))).scalars().all()
    delete_entries = [r for r in rows if r.action == "file.deleted"]
    assert len(delete_entries) == 1
    md = delete_entries[0].metadata_ or {}
    assert md.get("trash_path") is None
    assert md.get("reason") == "ghost cleanup"


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
async def test_scanner_malicious_disposition_marks_severity_crit(
    session: AsyncSession, tmp_path: Path
) -> None:
    """Stage 05 (v1.7) — the malicious disposition retains the
    ``crit`` severity but no longer sets quarantine columns
    (those columns are gone). Operators who want auto-delete on
    malicious extensions write a rule that matches ``severity eq
    crit`` and applies a Delete action."""
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
    # Stage 05: the quarantine columns are gone — confirm at the
    # attribute level so a future re-introduction (which would be
    # a regression) trips this test immediately.
    assert not hasattr(mf, "quarantined"), (
        "MediaFile.quarantined should be gone (Stage 05)"
    )


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
