"""v1.9 Stage 5.1 — RulesService wiring for ``search_upstream``.

End-to-end pins via RulesService.evaluate_file:
  1. Rule matches → provider.trigger_search called once with
     (config, media_file.path), audit log row created.
  2. Two ``search_upstream`` actions on the same rule, same
     integration → still called once (service-layer dedup).
  3. Integration not found → audit row written with
     status=error, provider never called.
  4. Integration disabled → audit row written with
     status=error, provider never called.
  5. Provider raises an exception → service captures it, writes
     status=error to the audit log, does NOT bubble.
  6. Target / integration kind mismatch → audit row with
     status=error, provider not called.
  7. Provider lacks ``trigger_search`` → audit row with
     status=error.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.registry import ServiceRegistry
from app.events.bus import get_event_bus
from app.integrations.types import IntegrationConfig, SearchTriggerResult
from app.main import create_app
from app.models.audit_log import AuditLogEntry
from app.models.integration import Integration
from app.models.library import Library
from app.models.media import MediaFile
from app.models.rule import Rule
from app.services.rules_service import RulesService
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database


@pytest_asyncio.fixture
async def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    db_path = tmp_path / "search_upstream.db"
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


# ── Fake provider for assertions ────────────────────────────────


class FakeProvider:
    """Records each call to ``trigger_search`` and returns the
    configured response. Optionally raises to exercise the
    service's exception-handling path."""

    kind = "sonarr"
    label = "Sonarr (fake)"
    config_schema = {"type": "object"}
    secret_fields: tuple[str, ...] = ()

    def __init__(
        self,
        result: SearchTriggerResult | None = None,
        *,
        raises: Exception | None = None,
        kind: str = "sonarr",
    ) -> None:
        self.calls: list[tuple[IntegrationConfig, str]] = []
        self._result = result or SearchTriggerResult(
            status="submitted", upstream_id="42", detail="ok"
        )
        self._raises = raises
        self.kind = kind

    async def trigger_search(
        self, config: IntegrationConfig, media_file_path: str
    ) -> SearchTriggerResult:
        self.calls.append((config, media_file_path))
        if self._raises is not None:
            raise self._raises
        return self._result


class FakeProviderWithoutSearch:
    """Provider that doesn't implement ``trigger_search`` —
    exercises the hasattr-skip path."""

    kind = "sonarr"
    label = "Sonarr (no search)"
    config_schema = {"type": "object"}
    secret_fields: tuple[str, ...] = ()


def _registry_with(kind: str, provider) -> ServiceRegistry:
    reg = ServiceRegistry()
    reg.register_capability(f"integration.{kind}", provider)
    return reg


# ── Seeding helpers ─────────────────────────────────────────────


async def _seed_basic(
    *, integration_kind: str = "sonarr", enabled: bool = True
) -> tuple[str, str, str]:
    """Seed a library, media file, integration, and a rule that
    fires a search_upstream action. Returns (rule_id,
    integration_id, media_file_id)."""
    import uuid

    async with get_database().session() as sess:
        lib_id = str(uuid.uuid4())
        sess.add(
            Library(
                id=lib_id,
                name="tv",
                root_path="/data/tv",
                enabled=True,
                kind="media",
            )
        )
        mf_id = str(uuid.uuid4())
        sess.add(
            MediaFile(
                id=mf_id,
                library_id=lib_id,
                path="/data/tv/Show/S01/ep01.mkv",
                relative_path="Show/S01/ep01.mkv",
                filename="ep01.mkv",
                extension="mkv",
                category="media",
                is_orphaned=True,
                size_bytes=1024,
                mtime=_dt.datetime.now(_dt.timezone.utc),
            )
        )
        integ_id = str(uuid.uuid4())
        sess.add(
            Integration(
                id=integ_id,
                name="sonarr-1",
                kind=integration_kind,
                enabled=enabled,
                config={"base_url": "http://sonarr.test"},
            )
        )
        sess.add(
            Rule(
                id=str(uuid.uuid4()),
                name="orphan → sonarr search",
                description=None,
                enabled=True,
                priority=100,
                definition={
                    "match": {
                        "field": "is_orphaned",
                        "op": "eq",
                        "value": True,
                    },
                    "actions": [
                        {
                            "type": "search_upstream",
                            "target": "sonarr",
                            "integration_id": integ_id,
                        }
                    ],
                },
            )
        )
        await sess.commit()
        rule_row = (
            await sess.execute(select(Rule).limit(1))
        ).scalar_one()
        return rule_row.id, integ_id, mf_id


async def _media(file_id: str) -> MediaFile:
    async with get_database().session() as sess:
        return (
            await sess.execute(
                select(MediaFile).where(MediaFile.id == file_id)
            )
        ).scalar_one()


async def _audit_rows(action: str) -> list[AuditLogEntry]:
    async with get_database().session() as sess:
        return (
            (
                await sess.execute(
                    select(AuditLogEntry).where(
                        AuditLogEntry.action == action
                    )
                )
            )
            .scalars()
            .all()
        )


# ── Tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_calls_provider_once_and_audits(
    client: AsyncClient,
) -> None:
    _, integ_id, mf_id = await _seed_basic()
    provider = FakeProvider(
        SearchTriggerResult(
            status="submitted",
            upstream_id="42",
            detail="SeriesSearch queued",
        )
    )
    registry = _registry_with("sonarr", provider)
    mf = await _media(mf_id)
    async with get_database().session() as sess:
        svc = RulesService(
            session=sess, event_bus=get_event_bus(), registry=registry
        )
        rules = await svc.load_enabled()
        await svc.evaluate_file(mf, rules)
        await sess.commit()
    # Provider called exactly once.
    assert len(provider.calls) == 1
    cfg, path = provider.calls[0]
    assert cfg.integration_id == integ_id
    assert path == "/data/tv/Show/S01/ep01.mkv"
    # Audit row recorded with submitted status.
    rows = await _audit_rows("rule.action.search_upstream")
    assert len(rows) == 1
    md = rows[0].metadata_ or {}
    assert md.get("status") == "submitted"
    assert md.get("upstream_id") == "42"
    assert md.get("integration_id") == integ_id
    assert md.get("target") == "sonarr"


@pytest.mark.asyncio
async def test_dedup_when_two_actions_target_same_integration(
    client: AsyncClient,
) -> None:
    """Two ``search_upstream`` actions on the same rule pointing
    at the same integration → provider called once."""
    import uuid

    async with get_database().session() as sess:
        lib_id = str(uuid.uuid4())
        sess.add(
            Library(
                id=lib_id,
                name="tv",
                root_path="/data/tv",
                enabled=True,
                kind="media",
            )
        )
        mf_id = str(uuid.uuid4())
        sess.add(
            MediaFile(
                id=mf_id,
                library_id=lib_id,
                path="/data/tv/X.mkv",
                relative_path="X.mkv",
                filename="X.mkv",
                extension="mkv",
                category="media",
                is_orphaned=True,
                size_bytes=1024,
                mtime=_dt.datetime.now(_dt.timezone.utc),
            )
        )
        integ_id = str(uuid.uuid4())
        sess.add(
            Integration(
                id=integ_id,
                name="sonarr",
                kind="sonarr",
                enabled=True,
                config={"base_url": "http://x"},
            )
        )
        sess.add(
            Rule(
                id=str(uuid.uuid4()),
                name="double-action",
                enabled=True,
                priority=100,
                definition={
                    "match": {
                        "field": "is_orphaned",
                        "op": "eq",
                        "value": True,
                    },
                    "actions": [
                        {
                            "type": "search_upstream",
                            "target": "sonarr",
                            "integration_id": integ_id,
                        },
                        {
                            "type": "search_upstream",
                            "target": "sonarr",
                            "integration_id": integ_id,
                        },
                    ],
                },
            )
        )
        await sess.commit()

    provider = FakeProvider()
    registry = _registry_with("sonarr", provider)
    mf = await _media(mf_id)
    async with get_database().session() as sess:
        svc = RulesService(
            session=sess, event_bus=get_event_bus(), registry=registry
        )
        rules = await svc.load_enabled()
        await svc.evaluate_file(mf, rules)
        await sess.commit()
    assert len(provider.calls) == 1
    rows = await _audit_rows("rule.action.search_upstream")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_integration_missing_writes_error_audit(
    client: AsyncClient,
) -> None:
    """Rule references an integration id that doesn't exist
    (operator deleted it) — service writes an error audit row,
    provider never invoked."""
    import uuid

    # Seed the rule with a bogus integration_id.
    async with get_database().session() as sess:
        lib_id = str(uuid.uuid4())
        sess.add(
            Library(
                id=lib_id,
                name="tv",
                root_path="/data/tv",
                enabled=True,
                kind="media",
            )
        )
        mf_id = str(uuid.uuid4())
        sess.add(
            MediaFile(
                id=mf_id,
                library_id=lib_id,
                path="/data/tv/X.mkv",
                relative_path="X.mkv",
                filename="X.mkv",
                extension="mkv",
                category="media",
                is_orphaned=True,
                size_bytes=1024,
                mtime=_dt.datetime.now(_dt.timezone.utc),
            )
        )
        sess.add(
            Rule(
                id=str(uuid.uuid4()),
                name="ghost-integration",
                enabled=True,
                priority=100,
                definition={
                    "match": {
                        "field": "is_orphaned",
                        "op": "eq",
                        "value": True,
                    },
                    "actions": [
                        {
                            "type": "search_upstream",
                            "target": "sonarr",
                            "integration_id": "does-not-exist",
                        }
                    ],
                },
            )
        )
        await sess.commit()

    provider = FakeProvider()
    registry = _registry_with("sonarr", provider)
    mf = await _media(mf_id)
    async with get_database().session() as sess:
        svc = RulesService(
            session=sess, event_bus=get_event_bus(), registry=registry
        )
        rules = await svc.load_enabled()
        await svc.evaluate_file(mf, rules)
        await sess.commit()
    assert provider.calls == []
    rows = await _audit_rows("rule.action.search_upstream")
    assert len(rows) == 1
    md = rows[0].metadata_ or {}
    assert md.get("status") == "error"
    assert "not found" in (md.get("detail") or "").lower()


@pytest.mark.asyncio
async def test_disabled_integration_writes_error_audit(
    client: AsyncClient,
) -> None:
    _, integ_id, mf_id = await _seed_basic(enabled=False)
    provider = FakeProvider()
    registry = _registry_with("sonarr", provider)
    mf = await _media(mf_id)
    async with get_database().session() as sess:
        svc = RulesService(
            session=sess, event_bus=get_event_bus(), registry=registry
        )
        rules = await svc.load_enabled()
        await svc.evaluate_file(mf, rules)
        await sess.commit()
    assert provider.calls == []
    rows = await _audit_rows("rule.action.search_upstream")
    assert len(rows) == 1
    md = rows[0].metadata_ or {}
    assert md.get("status") == "error"
    assert "disabled" in (md.get("detail") or "").lower()


@pytest.mark.asyncio
async def test_provider_exception_captured(client: AsyncClient) -> None:
    """A provider that raises must NOT abort the pipeline — its
    failure is captured and surfaced as a status=error audit row."""
    _, integ_id, mf_id = await _seed_basic()
    provider = FakeProvider(raises=RuntimeError("upstream is on fire"))
    registry = _registry_with("sonarr", provider)
    mf = await _media(mf_id)
    async with get_database().session() as sess:
        svc = RulesService(
            session=sess, event_bus=get_event_bus(), registry=registry
        )
        rules = await svc.load_enabled()
        # Must NOT raise.
        await svc.evaluate_file(mf, rules)
        await sess.commit()
    assert len(provider.calls) == 1
    rows = await _audit_rows("rule.action.search_upstream")
    assert len(rows) == 1
    md = rows[0].metadata_ or {}
    assert md.get("status") == "error"
    assert "upstream is on fire" in (md.get("detail") or "")


@pytest.mark.asyncio
async def test_target_kind_mismatch_writes_error(client: AsyncClient) -> None:
    """Rule says target=sonarr but the integration is kind=radarr
    (operator may have changed an integration's kind, or made a
    copy-paste mistake). Service refuses to call, writes an error."""
    import uuid

    async with get_database().session() as sess:
        lib_id = str(uuid.uuid4())
        sess.add(
            Library(
                id=lib_id,
                name="tv",
                root_path="/data/tv",
                enabled=True,
                kind="media",
            )
        )
        mf_id = str(uuid.uuid4())
        sess.add(
            MediaFile(
                id=mf_id,
                library_id=lib_id,
                path="/data/tv/X.mkv",
                relative_path="X.mkv",
                filename="X.mkv",
                extension="mkv",
                category="media",
                is_orphaned=True,
                size_bytes=1024,
                mtime=_dt.datetime.now(_dt.timezone.utc),
            )
        )
        integ_id = str(uuid.uuid4())
        # Integration is RADARR but rule says target=sonarr.
        sess.add(
            Integration(
                id=integ_id,
                name="r1",
                kind="radarr",
                enabled=True,
                config={"base_url": "http://r"},
            )
        )
        sess.add(
            Rule(
                id=str(uuid.uuid4()),
                name="mismatch",
                enabled=True,
                priority=100,
                definition={
                    "match": {
                        "field": "is_orphaned",
                        "op": "eq",
                        "value": True,
                    },
                    "actions": [
                        {
                            "type": "search_upstream",
                            "target": "sonarr",
                            "integration_id": integ_id,
                        }
                    ],
                },
            )
        )
        await sess.commit()

    provider = FakeProvider(kind="radarr")
    registry = _registry_with("radarr", provider)
    mf = await _media(mf_id)
    async with get_database().session() as sess:
        svc = RulesService(
            session=sess, event_bus=get_event_bus(), registry=registry
        )
        rules = await svc.load_enabled()
        await svc.evaluate_file(mf, rules)
        await sess.commit()
    assert provider.calls == []
    rows = await _audit_rows("rule.action.search_upstream")
    assert len(rows) == 1
    md = rows[0].metadata_ or {}
    assert md.get("status") == "error"
    assert "mismatch" in (md.get("detail") or "").lower() or "match" in (
        md.get("detail") or ""
    ).lower()


@pytest.mark.asyncio
async def test_provider_missing_trigger_search_writes_error(
    client: AsyncClient,
) -> None:
    """Provider plugin doesn't implement trigger_search — service
    skips with a recorded error."""
    _, _, mf_id = await _seed_basic()
    provider = FakeProviderWithoutSearch()
    registry = _registry_with("sonarr", provider)
    mf = await _media(mf_id)
    async with get_database().session() as sess:
        svc = RulesService(
            session=sess, event_bus=get_event_bus(), registry=registry
        )
        rules = await svc.load_enabled()
        await svc.evaluate_file(mf, rules)
        await sess.commit()
    rows = await _audit_rows("rule.action.search_upstream")
    assert len(rows) == 1
    md = rows[0].metadata_ or {}
    assert md.get("status") == "error"
    assert "trigger_search" in (md.get("detail") or "")
