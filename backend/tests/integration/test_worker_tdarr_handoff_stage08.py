"""Stage 08 (v1.7) — worker + poller end-to-end for Tdarr.

Plan §458 (worker side):
    Queue an item with ``routing_target="tdarr"``; assert the
    worker calls the right endpoint with the right body,
    transitions to ``routed``, and ``poll_routed_transcodes``
    moves it to ``completed`` when Tdarr reports done.

This file covers the worker integration: a mock IntegrationManager
+ mock TdarrProvider drive a queued item through ``submit`` →
``routed`` → ``poll`` → ``completed``, and confirm the bus events
fire at each transition (``optimization.routed``,
``optimization.routed_completed``).

We use stub providers rather than real httpx mocks because the
provider-side HTTP shape is already pinned in
``test_tdarr_handoff_stage08.py``. This file's job is to pin the
worker's coordination logic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.bus import EventBus, get_event_bus
from app.integrations.types import (
    HealthReport,
    IntegrationConfig,
    JobSubmitResult,
    TranscodeJobStatus,
)
from app.models.integration import Integration
from app.models.library import Library
from app.models.media import MediaFile
from app.models.optimization import OptimizationItem
from app.models.optimization_profile import OptimizationProfile
from app.optimization.poller import poll_routed_transcodes
from app.optimization.worker import OptimizationWorker
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database
from app.utils.datetime import utcnow


# ── Fake IntegrationManager + provider ─────────────────────────


class _FakeProvider:
    """Stub Tdarr provider whose submit/poll behaviour is set
    per-test via attributes. Conforms to the Stage 08 contract
    via ``hasattr`` on the worker side."""

    kind = "tdarr"
    label = "Tdarr (stub)"
    config_schema: dict[str, Any] = {}
    secret_fields: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.submit_calls: list[Any] = []
        self.status_calls: list[Any] = []
        self.submit_result: JobSubmitResult = JobSubmitResult(
            status="accepted",
            upstream_job_id="tdarr-test-1",
            detail="queued",
        )
        # Sequence of statuses to return, one per poll. We pop
        # from the front; once empty we return the last value
        # forever.
        self.status_sequence: list[TranscodeJobStatus] = [
            TranscodeJobStatus(status="completed", detail="ok")
        ]

    async def healthcheck(self, _config: IntegrationConfig) -> HealthReport:
        return HealthReport(status="ok")

    async def discover_libraries(self, _config: IntegrationConfig) -> list:
        return []

    async def sync_tags(self, _config: IntegrationConfig) -> list:
        return []

    async def fetch_playback_events(
        self, _config: IntegrationConfig, _since: Any
    ) -> list:
        return []

    async def submit_transcode_job(
        self, config: IntegrationConfig, job_spec: Any
    ) -> JobSubmitResult:
        self.submit_calls.append((config, job_spec))
        return self.submit_result

    async def list_transcode_profiles(
        self, _config: IntegrationConfig
    ) -> list:
        return []

    async def get_transcode_job_status(
        self, config: IntegrationConfig, upstream_job_id: str
    ) -> TranscodeJobStatus:
        self.status_calls.append((config, upstream_job_id))
        if self.status_sequence:
            if len(self.status_sequence) == 1:
                return self.status_sequence[0]
            return self.status_sequence.pop(0)
        # Never empty: callers should pre-populate before
        # polling. Return ``unknown`` as a safe default.
        return TranscodeJobStatus(status="unknown")


class _FakeManager:
    """Stand-in for ``IntegrationManager``. Just enough surface
    for the worker + poller to call against."""

    def __init__(self, provider: _FakeProvider) -> None:
        self._provider = provider

    def provider_for(self, kind: str) -> _FakeProvider | None:
        return self._provider if kind == "tdarr" else None

    def build_config(self, integration: Integration) -> IntegrationConfig:
        return IntegrationConfig(
            integration_id=integration.id,
            name=integration.name,
            kind=integration.kind,
            options=dict(integration.config or {}),
            secrets={},
        )


# ── Fixture ────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[AsyncSession, EventBus, _FakeProvider, _FakeManager]]:
    db_path = tmp_path / "opt08.db"
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

    provider = _FakeProvider()
    manager = _FakeManager(provider)

    try:
        async with db.session() as session:
            yield session, bus, provider, manager
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


async def _seed_tdarr_routed_item(
    session: AsyncSession,
    tmp_path: Path,
    *,
    provider_profile_id: str = "Tdarr_Plugin_henk_h265",
    integration_id_on_profile: str | None = "ig-tdarr-1",
) -> tuple[OptimizationItem, Integration | None]:
    media_dir = tmp_path / "media"
    media_dir.mkdir(exist_ok=True)
    input_path = media_dir / "movie.mkv"
    input_path.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 1024)

    lib = Library(name="Movies", root_path=str(media_dir), kind="movies")
    session.add(lib)
    await session.flush()
    media = MediaFile(
        library_id=lib.id,
        path=str(input_path),
        relative_path="movie.mkv",
        filename="movie.mkv",
        extension="mkv",
        category="media",
        size_bytes=1028,
        mtime=utcnow(),
    )
    session.add(media)
    await session.flush()

    integration: Integration | None = None
    if integration_id_on_profile:
        integration = Integration(
            id=integration_id_on_profile,
            name="tdarr-1",
            kind="tdarr",
            enabled=True,
            config={"base_url": "http://tdarr.test:8265"},
        )
        session.add(integration)
        await session.flush()

    profile = OptimizationProfile(
        name="tdarr-shrink",
        enabled=True,
        settings={
            "video": {"codec": "libx265", "crf": 22},
            "audio": {"codec": "copy"},
            "routing_target": "tdarr",
            "provider_metadata": {
                "provider_profile_id": provider_profile_id,
            },
        },
        optimization_integration_id=integration_id_on_profile,
    )
    session.add(profile)
    await session.flush()

    item = OptimizationItem(
        media_file_id=media.id,
        profile=profile.name,
        status="queued",
        queued_at=utcnow(),
    )
    session.add(item)
    await session.commit()
    return item, integration


# ── 1. Worker dispatches to provider on accepted ───────────────


@pytest.mark.asyncio
async def test_worker_submits_to_tdarr_when_routing_target_is_tdarr(
    env, tmp_path: Path,
) -> None:
    session, bus, provider, manager = env
    item, integration = await _seed_tdarr_routed_item(session, tmp_path)

    worker = OptimizationWorker(
        session=session, event_bus=bus, integration_manager=manager
    )
    report = await worker.run_one()

    # Provider was called exactly once with the right shape.
    assert len(provider.submit_calls) == 1
    config, job_spec = provider.submit_calls[0]
    assert config.kind == "tdarr"
    assert job_spec.item_id == item.id
    assert job_spec.input_path == str(tmp_path / "media" / "movie.mkv")
    # The profile editor's provider_metadata flows through to the
    # spec.
    assert job_spec.metadata.get("provider_profile_id") == "Tdarr_Plugin_henk_h265"

    # Item flipped to routed, upstream id stamped.
    await session.refresh(item)
    assert item.status == "routed"
    assert item.item_metadata["upstream_job_id"] == "tdarr-test-1"
    assert item.item_metadata["integration_id"] == integration.id  # type: ignore[union-attr]
    assert report.status == "routed"


@pytest.mark.asyncio
async def test_worker_emits_optimization_routed_event(
    env, tmp_path: Path,
) -> None:
    session, bus, provider, manager = env
    await _seed_tdarr_routed_item(session, tmp_path)

    received: list[dict[str, Any]] = []
    bus.subscribe(
        "optimization.routed",
        lambda e: received.append(dict(getattr(e, "payload", {}))),
    )

    worker = OptimizationWorker(
        session=session, event_bus=bus, integration_manager=manager
    )
    await worker.run_one()

    assert len(received) == 1
    payload = received[0]
    assert payload["routing_target"] == "tdarr"
    assert payload["upstream_job_id"] == "tdarr-test-1"


# ── 2. Worker handles rejection terminally ─────────────────────


@pytest.mark.asyncio
async def test_worker_fails_item_when_provider_rejects(
    env, tmp_path: Path,
) -> None:
    session, bus, provider, manager = env
    provider.submit_result = JobSubmitResult(
        status="rejected",
        detail="missing provider_profile_id",
    )
    item, _ = await _seed_tdarr_routed_item(session, tmp_path)

    worker = OptimizationWorker(
        session=session, event_bus=bus, integration_manager=manager
    )
    report = await worker.run_one()

    await session.refresh(item)
    assert item.status == "failed"
    assert "missing provider_profile_id" in (item.error or "")
    assert report.status == "failed"


# ── 3. Worker re-queues on transient provider error ────────────


@pytest.mark.asyncio
async def test_worker_requeues_on_transient_provider_error(
    env, tmp_path: Path,
) -> None:
    """``status="error"`` from the provider = transient. The
    worker re-queues so the next tick re-tries."""
    session, bus, provider, manager = env
    provider.submit_result = JobSubmitResult(
        status="error",
        detail="Tdarr HTTP 503",
    )
    item, _ = await _seed_tdarr_routed_item(session, tmp_path)

    worker = OptimizationWorker(
        session=session, event_bus=bus, integration_manager=manager
    )
    report = await worker.run_one()

    await session.refresh(item)
    assert item.status == "queued"  # re-queued, not failed
    assert report.status == "skipped"
    assert "Tdarr HTTP 503" in (report.detail or "")


# ── 4. Worker fails when no integration is configured ──────────


@pytest.mark.asyncio
async def test_worker_fails_when_profile_lacks_integration_id(
    env, tmp_path: Path,
) -> None:
    session, bus, provider, manager = env
    item, _ = await _seed_tdarr_routed_item(
        session, tmp_path, integration_id_on_profile=None
    )

    worker = OptimizationWorker(
        session=session, event_bus=bus, integration_manager=manager
    )
    report = await worker.run_one()

    await session.refresh(item)
    assert item.status == "failed"
    assert "integration is configured" in (item.error or "")
    assert report.status == "failed"
    # Provider was never called.
    assert provider.submit_calls == []


# ── 5. Poller completes a routed item ──────────────────────────


@pytest.mark.asyncio
async def test_poll_routed_transcodes_advances_completed_item(
    env, tmp_path: Path,
) -> None:
    """Plan §458 (poller side) — when Tdarr reports completion,
    the poller flips the item to ``completed`` and emits
    ``optimization.routed_completed``."""
    session, bus, provider, manager = env
    item, _ = await _seed_tdarr_routed_item(session, tmp_path)

    # First, submit to get the item into ``routed`` state.
    worker = OptimizationWorker(
        session=session, event_bus=bus, integration_manager=manager
    )
    await worker.run_one()
    await session.refresh(item)
    assert item.status == "routed"

    # Now poll — provider's default sequence returns "completed".
    received: list[dict[str, Any]] = []
    bus.subscribe(
        "optimization.routed_completed",
        lambda e: received.append(dict(getattr(e, "payload", {}))),
    )

    report = await poll_routed_transcodes(
        session=session, integration_manager=manager, event_bus=bus
    )

    await session.refresh(item)
    assert item.status == "completed"
    assert item.progress_pct == 100
    assert report.checked == 1
    assert report.completed == 1
    assert report.failed == 0
    # Bus event fired.
    assert len(received) == 1
    assert received[0]["item_id"] == item.id
    assert received[0]["upstream_job_id"] == "tdarr-test-1"


# ── 6. Poller fails an item when provider reports failed ───────


@pytest.mark.asyncio
async def test_poll_routed_transcodes_advances_failed_item(
    env, tmp_path: Path,
) -> None:
    """Provider reports failed → item flips to failed +
    ``optimization.routed_failed`` fires."""
    session, bus, provider, manager = env
    provider.status_sequence = [
        TranscodeJobStatus(status="failed", detail="Tdarr transcode error"),
    ]
    item, _ = await _seed_tdarr_routed_item(session, tmp_path)

    worker = OptimizationWorker(
        session=session, event_bus=bus, integration_manager=manager
    )
    await worker.run_one()

    received: list[dict[str, Any]] = []
    bus.subscribe(
        "optimization.routed_failed",
        lambda e: received.append(dict(getattr(e, "payload", {}))),
    )

    report = await poll_routed_transcodes(
        session=session, integration_manager=manager, event_bus=bus
    )

    await session.refresh(item)
    assert item.status == "failed"
    assert "Tdarr transcode error" in (item.error or "")
    assert report.failed == 1
    assert len(received) == 1


# ── 7. Poller leaves running items alone, updates progress ─────


@pytest.mark.asyncio
async def test_poll_routed_transcodes_leaves_running_items_routed(
    env, tmp_path: Path,
) -> None:
    """``running`` status from the provider = still in flight.
    Item stays in ``routed`` and progress_pct updates if
    provided."""
    session, bus, provider, manager = env
    provider.status_sequence = [
        TranscodeJobStatus(status="running", progress_pct=42),
    ]
    item, _ = await _seed_tdarr_routed_item(session, tmp_path)

    worker = OptimizationWorker(
        session=session, event_bus=bus, integration_manager=manager
    )
    await worker.run_one()

    report = await poll_routed_transcodes(
        session=session, integration_manager=manager, event_bus=bus
    )

    await session.refresh(item)
    assert item.status == "routed"
    assert item.progress_pct == 42
    assert report.checked == 1
    assert report.still_running == 1
    assert report.completed == 0
    assert report.failed == 0


# ── 8. Poller is no-op when there are no routed items ──────────


@pytest.mark.asyncio
async def test_poll_no_routed_items_is_noop(env) -> None:
    session, bus, _provider, manager = env
    report = await poll_routed_transcodes(
        session=session, integration_manager=manager, event_bus=bus
    )
    assert report.checked == 0
    assert report.completed == 0
    assert report.failed == 0


# ── 9. Poller isolates per-item errors ─────────────────────────


@pytest.mark.asyncio
async def test_poll_isolates_provider_crashes(
    env, tmp_path: Path,
) -> None:
    """One provider raising shouldn't stop the rest of the
    batch. The crashing item stays routed; report.errored
    increments."""
    session, bus, provider, manager = env

    item1, _ = await _seed_tdarr_routed_item(session, tmp_path)

    # Submit item1 so it becomes routed.
    worker = OptimizationWorker(
        session=session, event_bus=bus, integration_manager=manager
    )
    await worker.run_one()

    # Add a SECOND routed item under a different profile name
    # (the OptimizationItem unique constraint is on (media_file_id,
    # profile)). Stamp the polling correlation metadata directly.
    second_profile = OptimizationProfile(
        name="tdarr-shrink-2",
        enabled=True,
        settings={
            "video": {"codec": "libx265", "crf": 22},
            "audio": {"codec": "copy"},
            "routing_target": "tdarr",
            "provider_metadata": {"provider_profile_id": "x"},
        },
        optimization_integration_id="ig-tdarr-1",
    )
    session.add(second_profile)
    await session.flush()
    item2 = OptimizationItem(
        media_file_id=item1.media_file_id,
        profile=second_profile.name,
        status="routed",
        queued_at=utcnow(),
        item_metadata={
            "upstream_job_id": "tdarr-test-2",
            "integration_id": "ig-tdarr-1",
        },
    )
    session.add(item2)
    await session.commit()

    # Now make the provider crash on the second call.
    original_get = provider.get_transcode_job_status
    call_count = {"n": 0}

    async def _flaky_get(config: IntegrationConfig, upstream_job_id: str):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("provider boom")
        return await original_get(config, upstream_job_id)

    provider.get_transcode_job_status = _flaky_get  # type: ignore[method-assign]

    report = await poll_routed_transcodes(
        session=session, integration_manager=manager, event_bus=bus
    )

    # Two items checked, one errored (crash), one completed.
    assert report.checked == 2
    assert report.errored == 1
    assert report.completed == 1
