"""Stage 03 — built-in rules seed correctly on an empty DB.

Plan §240: "bring up an empty DB, run ``register_builtin_rules``,
assert the new rules are inserted and ``DISABLED_BY_DEFAULT``
entries are seeded as ``enabled=False``."

We don't re-validate the Stage 29 seeding contract (idempotency,
conflict handling, etc.) — that's already covered by
``test_rules_builtin_stage29.py``. This test focuses on the
Stage 03 delta only: the seven new names appear, with the
expected enabled state.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from app.events.bus import get_event_bus
from app.rules.builtin import (
    BUILTIN_RULES,
    DISABLED_BY_DEFAULT,
    register_builtin_rules,
)
from app.services.repositories import RuleRepository
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database

STAGE_03_RULE_NAMES = frozenset(
    {
        "Plex incompatible video codec",
        "Plex incompatible audio codec",
        "Jellyfin incompatible video codec",
        "Jellyfin incompatible audio codec",
        "Likely transcode trigger (4K HEVC 10-bit)",
        "Executable file in library",
        "Non-media file extension",
    }
)


@pytest_asyncio.fixture
async def seeded_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[None]:
    db_path = tmp_path / "stage03_builtins.db"
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

    # Run the seeder on the empty DB.
    async with db.session() as sess:
        result = await register_builtin_rules(sess)
        # Sanity: every builtin was inserted on this fresh DB.
        assert result["inserted"] == len(BUILTIN_RULES)
        assert result["conflicts"] == 0

    try:
        yield None
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


@pytest.mark.asyncio
async def test_all_stage_03_builtin_rules_are_seeded(
    seeded_db: None,
) -> None:
    """Every Stage 03 rule name appears in the seeded rules table."""
    async with get_database().session() as sess:
        repo = RuleRepository(sess)
        for name in STAGE_03_RULE_NAMES:
            row = await repo.get_by_name(name)
            assert row is not None, f"missing builtin: {name}"
            assert row.is_builtin is True


@pytest.mark.asyncio
async def test_non_media_file_extension_is_seeded_enabled_after_stage_06(
    seeded_db: None,
) -> None:
    """Stage 03 originally seeded this rule disabled (the 'junk'
    category didn't exist yet). Stage 06 (plan §363) flips it on
    now that Stage 05's extension-classifier populates 'junk'.

    Inverts the Stage 03 assertion."""
    async with get_database().session() as sess:
        repo = RuleRepository(sess)
        row = await repo.get_by_name("Non-media file extension")
        assert row is not None
        assert row.enabled is True
        # Sanity: tag-only action.
        actions = row.definition["actions"]
        assert all(a["type"] == "add_tag" for a in actions)
        assert {a["tag"] for a in actions} == {"junk-extension"}


@pytest.mark.asyncio
async def test_stage_03_enabled_rules_are_actually_enabled(
    seeded_db: None,
) -> None:
    """The six non-junk Stage 03 rules ship enabled. Only
    ``Non-media file extension`` is in DISABLED_BY_DEFAULT."""
    async with get_database().session() as sess:
        repo = RuleRepository(sess)
        for name in STAGE_03_RULE_NAMES:
            row = await repo.get_by_name(name)
            assert row is not None
            expected_enabled = name not in DISABLED_BY_DEFAULT
            assert row.enabled is expected_enabled, (
                f"{name}: expected enabled={expected_enabled}, "
                f"got enabled={row.enabled}"
            )


@pytest.mark.asyncio
async def test_repeat_seed_is_idempotent_for_stage_03_rules(
    seeded_db: None,
) -> None:
    """Running the seeder a second time refreshes (or no-ops)
    the rules but doesn't duplicate them."""
    async with get_database().session() as sess:
        result = await register_builtin_rules(sess)
    # On a re-run nothing is INSERTED; the new rules are either
    # ``unchanged`` (descriptions match) or ``refreshed`` (a doc
    # edit landed). Both are fine; INSERTED would mean a
    # duplicate-name bug.
    assert result["inserted"] == 0
    assert result["conflicts"] == 0

    async with get_database().session() as sess:
        repo = RuleRepository(sess)
        # Each name still maps to exactly one row.
        for name in STAGE_03_RULE_NAMES:
            row = await repo.get_by_name(name)
            assert row is not None
