"""Stage 06 (v1.7) — notification throttle test.

Plan §373:
    Fire 10 events into a rule with ``throttle: {window_seconds:
    60, max_per_window: 2}``; assert 2 notifications sent.

This test focuses on the throttle gate at the service level —
``RulesService._throttle_gate`` is the public-ish unit that the
dispatch loop calls. We exercise it directly rather than going
through the full evaluator → service → dispatcher pipeline,
because:

  1. The Stage 06 contract is "after the gate, dispatch happens
     normally" — testing the dispatch is testing pre-Stage-06 code.
  2. The gate's contract is small and verifiable: returns True for
     the first ``max_per_window`` calls in a window, False
     thereafter; writes one audit-log row per window per rule.

A separate (smaller) test runs an end-to-end pass through a full
rules evaluation against a rule with a Notify(throttle=...) action
and confirms the row counter reaches the expected value.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.events.bus import EventBus
from app.models.audit_log import AuditLogEntry
from app.models.rule import Rule
from app.models.rule_notification_window import RuleNotificationWindow
from app.services.repositories import RuleRepository
from app.services.rules_service import RulesService
from app.storage.base import Base
from app.storage.database import get_database


@pytest_asyncio.fixture
async def session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncSession]:
    db_path = tmp_path / "stage06_throttle.db"
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
async def test_throttle_gate_allows_up_to_max_then_suppresses(
    session: AsyncSession,
) -> None:
    """Per plan §373 — 10 events with throttle (60s, 2/window)
    should yield 2 allowed dispatches, 8 suppressed."""
    # Seed a rule so the FK on rule_notification_windows.rule_id
    # is satisfied.
    rule = Rule(
        name="throttled-rule",
        description="",
        enabled=True,
        definition={
            "match": {"field": "extension", "op": "eq", "value": "mkv"},
            "actions": [
                {
                    "type": "notify",
                    "channel": "ops",
                    "throttle": {
                        "window_seconds": 60,
                        "max_per_window": 2,
                    },
                }
            ],
        },
    )
    await RuleRepository(session).add(rule)
    await session.commit()

    service = RulesService(session=session, event_bus=EventBus())
    # Anchor a single ``now`` so all 10 calls land in the same
    # 60-second window. (If ``utcnow()`` rolled over a window
    # boundary mid-test the counts would split across two rows.)
    now = datetime(2026, 5, 16, 12, 0, 30, tzinfo=UTC)

    allowed_count = 0
    suppressed_count = 0
    for _ in range(10):
        allowed = await service._throttle_gate(
            rule_id=rule.id,
            rule_name=rule.name,
            window_seconds=60,
            max_per_window=2,
            now=now,
        )
        if allowed:
            allowed_count += 1
        else:
            suppressed_count += 1

    assert allowed_count == 2, (
        f"expected exactly 2 allowed dispatches, got {allowed_count}"
    )
    assert suppressed_count == 8

    # The window row's count tracks total attempts (allowed +
    # suppressed) — 10. The plan's "1 row per (rule, window)"
    # contract is verified by the unique constraint plus a count
    # check.
    rows = (
        await session.execute(
            select(RuleNotificationWindow).where(
                RuleNotificationWindow.rule_id == rule.id
            )
        )
    ).scalars().all()
    assert len(rows) == 1, f"expected one window row, got {len(rows)}"
    assert rows[0].count == 10


@pytest.mark.asyncio
async def test_throttle_gate_audit_log_once_per_window(
    session: AsyncSession,
) -> None:
    """Per addendum A.2 §125 — one summary audit entry per
    (rule, window), not per suppressed event."""
    rule = Rule(
        name="once-per-window",
        description="",
        enabled=True,
        definition={
            "match": {"field": "extension", "op": "eq", "value": "mkv"},
            "actions": [
                {
                    "type": "notify",
                    "channel": "ops",
                    "throttle": {
                        "window_seconds": 60,
                        "max_per_window": 1,
                    },
                }
            ],
        },
    )
    await RuleRepository(session).add(rule)
    await session.commit()

    service = RulesService(session=session, event_bus=EventBus())
    now = datetime(2026, 5, 16, 12, 0, 30, tzinfo=UTC)

    # 1st call: allowed.
    assert await service._throttle_gate(
        rule_id=rule.id,
        rule_name=rule.name,
        window_seconds=60,
        max_per_window=1,
        now=now,
    )
    # Next 5 calls: all suppressed. Only the FIRST should write
    # an audit log; the rest should bump count silently.
    for _ in range(5):
        assert not await service._throttle_gate(
            rule_id=rule.id,
            rule_name=rule.name,
            window_seconds=60,
            max_per_window=1,
            now=now,
        )
    await session.commit()

    audit_rows = (
        await session.execute(
            select(AuditLogEntry).where(
                AuditLogEntry.action == "rule.throttled"
            )
        )
    ).scalars().all()
    assert len(audit_rows) == 1, (
        f"expected exactly 1 throttle audit entry per window, "
        f"got {len(audit_rows)}"
    )
    entry = audit_rows[0]
    assert entry.target_id == rule.id
    assert entry.target_type == "rule"
    assert entry.actor_label == "rules"
    md = entry.metadata_ or {}
    assert md.get("rule_name") == "once-per-window"
    assert md.get("max_per_window") == 1


@pytest.mark.asyncio
async def test_throttle_gate_emits_bus_event_on_each_suppression(
    session: AsyncSession,
) -> None:
    """Plan §A.1 §113 — ``rule.throttled`` is emitted on every
    suppressed event (not throttled itself — the dashboard
    consumer aggregates). The audit log is the once-per-window
    surface; the bus event is per-suppression."""
    rule = Rule(
        name="bus-event-test",
        description="",
        enabled=True,
        definition={
            "match": {"field": "extension", "op": "eq", "value": "mkv"},
            "actions": [
                {
                    "type": "notify",
                    "channel": "ops",
                    "throttle": {
                        "window_seconds": 60,
                        "max_per_window": 2,
                    },
                }
            ],
        },
    )
    await RuleRepository(session).add(rule)
    await session.commit()

    bus = EventBus()
    bus.clear()
    throttled_events: list[dict[str, Any]] = []
    bus.subscribe(
        "rule.throttled",
        lambda e: throttled_events.append(dict(getattr(e, "payload", {}))),
    )

    service = RulesService(session=session, event_bus=bus)
    now = datetime(2026, 5, 16, 12, 0, 30, tzinfo=UTC)

    # 5 calls into a 2-per-window throttle = 3 suppressions.
    for _ in range(5):
        await service._throttle_gate(
            rule_id=rule.id,
            rule_name=rule.name,
            window_seconds=60,
            max_per_window=2,
            now=now,
        )

    assert len(throttled_events) == 3, (
        f"expected 3 rule.throttled events, got {len(throttled_events)}"
    )
    payload = throttled_events[0]
    assert payload["rule_id"] == rule.id
    assert payload["rule_name"] == "bus-event-test"
    assert payload["max_per_window"] == 2
    # window_start + window_end are ISO strings.
    assert "window_start" in payload
    assert "window_end" in payload


@pytest.mark.asyncio
async def test_throttle_gate_separate_windows_have_separate_rows(
    session: AsyncSession,
) -> None:
    """Two ``now`` timestamps in different 60-second buckets
    produce two rows; each window has its own count + audit
    entry. Verifies the window-flooring logic doesn't
    accidentally bucket cross-window events together."""
    rule = Rule(
        name="multi-window",
        description="",
        enabled=True,
        definition={
            "match": {"field": "extension", "op": "eq", "value": "mkv"},
            "actions": [
                {
                    "type": "notify",
                    "channel": "ops",
                    "throttle": {
                        "window_seconds": 60,
                        "max_per_window": 1,
                    },
                }
            ],
        },
    )
    await RuleRepository(session).add(rule)
    await session.commit()

    service = RulesService(session=session, event_bus=EventBus())
    # Window 1: 12:00:00 - 12:00:59 (matches @ 12:00:30)
    now1 = datetime(2026, 5, 16, 12, 0, 30, tzinfo=UTC)
    # Window 2: 12:01:00 - 12:01:59 (matches @ 12:01:15)
    now2 = datetime(2026, 5, 16, 12, 1, 15, tzinfo=UTC)

    # First call in window 1 allowed.
    assert await service._throttle_gate(
        rule_id=rule.id, rule_name=rule.name,
        window_seconds=60, max_per_window=1, now=now1,
    )
    # Second call in window 1 suppressed.
    assert not await service._throttle_gate(
        rule_id=rule.id, rule_name=rule.name,
        window_seconds=60, max_per_window=1, now=now1,
    )
    # First call in window 2 allowed (fresh window).
    assert await service._throttle_gate(
        rule_id=rule.id, rule_name=rule.name,
        window_seconds=60, max_per_window=1, now=now2,
    )
    await session.commit()

    rows = (
        await session.execute(
            select(RuleNotificationWindow)
            .where(RuleNotificationWindow.rule_id == rule.id)
            .order_by(RuleNotificationWindow.window_start)
        )
    ).scalars().all()
    assert len(rows) == 2
    assert rows[0].count == 2  # window 1: 1 allowed + 1 suppressed
    assert rows[1].count == 1  # window 2: 1 allowed
