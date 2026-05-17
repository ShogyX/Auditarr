"""Tests for the settings hot-reload mechanism (Stage 21).

The full cross-process reload uses Redis pubsub on the
``auditarr:settings:reload`` channel — testing that end-to-end
requires a live Redis instance and two processes, which is heavy
for a unit test. What we CAN pin without that infrastructure:

1. ``load_and_apply_overrides`` mutates the in-process Settings
   instance to reflect every persisted override.
2. Setting an override via the service updates the live Settings
   instance immediately (so the next request sees the new value
   without a restart).
3. Clearing an override restores the env-driven default.
4. Side effects (log_level → live stdlib logger) fire on apply.
5. Invalid DB rows are skipped with a warning, not raised — a
   tightened range in a future release can't crash startup.

The pubsub publish itself is best-effort (try/except in the
service) and tested by a separate integration test with a fake
Redis client below.
"""

from __future__ import annotations

import logging

import pytest
import pytest_asyncio

from app.core.runtime_settings_schema import RuntimeSettingValidationError
from app.core.settings import get_settings
from app.models.runtime_setting import RuntimeSettingOverride
from app.services.runtime_settings import (
    RuntimeSettingsService,
    load_and_apply_overrides,
)
from app.storage.base import Base
from app.storage.database import get_database


@pytest_asyncio.fixture
async def db_and_settings(tmp_path, monkeypatch):
    """Spin up the DB + a fresh Settings instance and tear down after."""
    monkeypatch.setenv(
        "AUDITARR_DATABASE_URL",
        f"sqlite+aiosqlite:///{tmp_path / 'reload.db'}",
    )
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    get_settings.cache_clear()
    settings = get_settings()
    db = get_database()
    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield db, settings
    finally:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        get_settings.cache_clear()


# ── In-process apply ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_load_and_apply_mutates_settings(db_and_settings) -> None:
    """A DB override should land on the in-process Settings instance
    when load_and_apply_overrides runs."""
    db, settings = db_and_settings
    # Pre-populate the DB with an override row directly — simulate
    # what restart-time loading reads.
    async with db.session() as sess:
        sess.add(
            RuntimeSettingOverride(
                key="scanner_worker_concurrency", value=12,
            )
        )
        await sess.commit()
    assert settings.scanner_worker_concurrency == 4  # baseline

    async with db.session() as sess:
        await load_and_apply_overrides(sess, settings)

    assert settings.scanner_worker_concurrency == 12


@pytest.mark.asyncio
async def test_set_override_updates_settings_immediately(
    db_and_settings,
) -> None:
    """When the service writes an override, the in-process Settings
    instance must reflect the new value before the call returns."""
    db, settings = db_and_settings
    assert settings.access_token_ttl_minutes == 30  # baseline

    async with db.session() as sess:
        svc = RuntimeSettingsService(session=sess, settings=settings)
        await svc.set_override("access_token_ttl_minutes", 5)

    assert settings.access_token_ttl_minutes == 5


@pytest.mark.asyncio
async def test_clear_override_restores_default(db_and_settings) -> None:
    db, settings = db_and_settings

    async with db.session() as sess:
        svc = RuntimeSettingsService(session=sess, settings=settings)
        await svc.set_override("access_token_ttl_minutes", 5)
    assert settings.access_token_ttl_minutes == 5

    async with db.session() as sess:
        svc = RuntimeSettingsService(session=sess, settings=settings)
        removed = await svc.clear_override("access_token_ttl_minutes")
    assert removed is True
    # Restored to the env default — Settings model declares 30.
    assert settings.access_token_ttl_minutes == 30


# ── Side effects ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_log_level_side_effect_fires_on_apply(
    db_and_settings,
) -> None:
    """Setting log_level via the service must update the stdlib
    root logger — otherwise the runtime change has no observable
    effect until the next restart."""
    db, settings = db_and_settings
    # Whatever it was, force a known starting point.
    logging.getLogger().setLevel(logging.INFO)

    async with db.session() as sess:
        svc = RuntimeSettingsService(session=sess, settings=settings)
        await svc.set_override("log_level", "debug")

    assert logging.getLogger().level == logging.DEBUG
    # Reset so we don't bleed into other tests.
    logging.getLogger().setLevel(logging.WARNING)


@pytest.mark.asyncio
async def test_log_level_side_effect_fires_on_startup_apply(
    db_and_settings,
) -> None:
    """Same contract, but coming via the startup load path rather
    than the write path — a process that reboots with a persisted
    log_level override must end up at the right level."""
    db, settings = db_and_settings
    logging.getLogger().setLevel(logging.INFO)

    async with db.session() as sess:
        sess.add(RuntimeSettingOverride(key="log_level", value="error"))
        await sess.commit()

    async with db.session() as sess:
        await load_and_apply_overrides(sess, settings)

    assert logging.getLogger().level == logging.ERROR
    logging.getLogger().setLevel(logging.WARNING)


# ── Resilience ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_invalid_db_row_skipped_not_raised(db_and_settings) -> None:
    """If the schema was tightened between releases and an old DB
    row no longer fits the new range, load_and_apply must skip it
    with a warning rather than crash startup. The Settings instance
    keeps the env default for that key."""
    db, settings = db_and_settings
    # Stuff a value that exceeds the schema's upper bound.
    async with db.session() as sess:
        sess.add(
            RuntimeSettingOverride(
                key="access_token_ttl_minutes", value=999999,
            )
        )
        await sess.commit()

    # This must NOT raise.
    async with db.session() as sess:
        await load_and_apply_overrides(sess, settings)

    # And the in-process value is the env default, not the bad row.
    assert settings.access_token_ttl_minutes == 30


@pytest.mark.asyncio
async def test_unknown_key_in_db_skipped_not_raised(
    db_and_settings,
) -> None:
    """An override row whose key was removed from the schema (e.g.
    a deprecated field) is logged and skipped, not crash-raised."""
    db, settings = db_and_settings
    async with db.session() as sess:
        sess.add(
            RuntimeSettingOverride(
                key="deprecated_old_setting", value="anything",
            )
        )
        await sess.commit()

    async with db.session() as sess:
        await load_and_apply_overrides(sess, settings)


# ── Service-layer rejection ──────────────────────────────────
@pytest.mark.asyncio
async def test_clear_unknown_key_raises_validation_error(
    db_and_settings,
) -> None:
    """The API layer turns this into a 422; pin the underlying
    exception type at the service layer."""
    db, settings = db_and_settings
    async with db.session() as sess:
        svc = RuntimeSettingsService(session=sess, settings=settings)
        with pytest.raises(RuntimeSettingValidationError):
            await svc.clear_override("secret_key")


# ── Publish best-effort ──────────────────────────────────────
@pytest.mark.asyncio
async def test_set_override_does_not_fail_when_redis_unavailable(
    db_and_settings, monkeypatch,
) -> None:
    """The reload publish is best-effort — a Redis outage must not
    fail the write. The in-process apply already happened by the
    time we try to publish.

    We force the failure by making get_redis() raise.
    """
    db, settings = db_and_settings

    from app.storage import cache

    def broken_get_redis():
        raise RuntimeError("simulated redis outage")

    monkeypatch.setattr(cache, "get_redis", broken_get_redis)

    # The write must still succeed end-to-end.
    async with db.session() as sess:
        svc = RuntimeSettingsService(session=sess, settings=settings)
        await svc.set_override("scanner_worker_concurrency", 8)
    assert settings.scanner_worker_concurrency == 8
