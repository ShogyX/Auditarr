"""Runtime settings service (Stage 21).

Three responsibilities:

1. **Bootstrap.** On startup, load overrides from the DB and apply
   them to the in-process :class:`Settings` instance. Existing code
   that reads ``settings.foo`` then automatically sees the override
   value with no call-site changes.

2. **Writes.** Validate the incoming value against
   :func:`validate_runtime_setting`, persist to the DB, apply
   in-process, and publish a reload notification to Redis so other
   processes (notably the worker) pick up the change too.

3. **Subscribe.** Each process runs a background task that listens
   on the reload channel. When a message arrives the task re-loads
   overrides from the DB and re-applies them. This is how the
   worker picks up an API-initiated change without a restart.

Apply semantics: the pydantic ``Settings`` model is mutable by
default, so we simply assign ``setattr(settings, key, value)``. The
``log_level`` override has an extra side-effect — it pushes the new
level into the live stdlib logger — handled by
:func:`_apply_side_effects`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.runtime_settings_schema import (
    RUNTIME_EDITABLE_BY_KEY,
    SECRETS_BY_KEY,
    RuntimeSettingValidationError,
    validate_runtime_setting,
    validate_secret,
)
from app.core.settings import Settings
from app.security.secrets import get_secret_box
from app.services.repositories.runtime_settings import (
    RuntimeOverrideRepository,
    RuntimeSettingChangeRepository,
    SecretRepository,
)

log = structlog.get_logger(category="settings")

# Redis pubsub channel for hot-reload notifications.
RELOAD_CHANNEL = "auditarr:settings:reload"


def _apply_side_effects(settings: Settings, key: str) -> None:
    """Run anything that has to happen beyond setting the attribute.

    Most keys are pure data — the value is read on the next request
    that needs it. A small set of keys also drive shared in-process
    state (the stdlib log level being the canonical example) and
    need a kick to take effect.
    """
    if key == "log_level":
        level = getattr(logging, settings.log_level.upper(), None)
        if level is not None:
            logging.getLogger().setLevel(level)
            log.info("settings.log_level_applied", level=settings.log_level)
    # Future keys with side-effects go here. We deliberately don't
    # have anything for the scanner concurrency or webhook timeout
    # values — those are read at the start of each batch/request, so
    # the bare attribute mutation suffices.


async def _apply_overrides_inproc(
    settings: Settings, overrides: dict[str, Any]
) -> None:
    """Apply the given ``{key: value}`` map to the in-process Settings
    instance, then run side-effects.

    Unknown keys are skipped with a warning — they typically mean
    the schema was tightened and an old DB row no longer corresponds
    to an editable field. The DB row is left in place so an operator
    can see + remove it.
    """
    for key, value in overrides.items():
        if key not in RUNTIME_EDITABLE_BY_KEY:
            log.warning(
                "settings.skipping_unknown_override",
                key=key,
                hint="This override key isn't on the runtime-editable "
                "whitelist (anymore). The DB row is preserved but ignored.",
            )
            continue
        # Re-validate to catch DB rows that no longer fit the schema
        # (e.g. value range was tightened in a release). Skip with a
        # loud warning rather than crash — the env default is safer
        # than a hard failure on startup.
        try:
            validated = validate_runtime_setting(key, value)
        except RuntimeSettingValidationError as exc:
            log.warning(
                "settings.invalid_override_skipped",
                key=key,
                error=str(exc),
                hint="DB override fails current schema validation; using "
                "env default. Re-set the value via the UI to clear.",
            )
            continue
        setattr(settings, key, validated)
        _apply_side_effects(settings, key)
    log.info(
        "settings.overrides_applied",
        count=len(overrides),
        keys=sorted(overrides.keys()),
    )


# ── Bootstrap (called from the FastAPI lifespan + worker startup) ──
async def load_and_apply_overrides(
    session: AsyncSession, settings: Settings
) -> None:
    """Read all overrides from the DB and apply them to ``settings``.

    Idempotent: calling twice produces the same in-process state.
    Used by:
    * the API lifespan handler at startup
    * the worker startup hook
    * the pubsub listener after every reload signal
    """
    repo = RuntimeOverrideRepository(session)
    overrides = await repo.list_all()
    await _apply_overrides_inproc(settings, overrides)


# ── Writes ────────────────────────────────────────────────────
class RuntimeSettingsService:
    """Façade the API uses for read + write + reset operations."""

    def __init__(self, *, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._repo = RuntimeOverrideRepository(session)
        # Stage 2: audit log of every override change. Same session
        # as the override repo so the audit write lands in the same
        # transaction.
        self._audit = RuntimeSettingChangeRepository(session)

    async def list_effective(self) -> dict[str, dict[str, Any]]:
        """Return ``{key: {value, is_override}}`` for every editable
        field — the value the app is actually using right now, plus a
        flag saying whether that came from an override or the env
        default.

        Used by the UI to render "what's customized vs what's stock".
        """
        overrides = await self._repo.list_all()
        out: dict[str, dict[str, Any]] = {}
        for key, spec in RUNTIME_EDITABLE_BY_KEY.items():
            if key in overrides:
                out[key] = {
                    "value": overrides[key],
                    "is_override": True,
                    "env_default": spec.field_default,
                }
            else:
                # Read the actual env-resolved value off Settings —
                # so an operator who set ``AUDITARR_LOG_LEVEL=debug``
                # in the env file sees "debug" here, not the
                # schema's hardcoded "info".
                out[key] = {
                    "value": getattr(self._settings, key),
                    "is_override": False,
                    "env_default": spec.field_default,
                }
        return out

    async def set_override(
        self,
        key: str,
        value: Any,
        *,
        set_by_user_id: str | None = None,
    ) -> Any:
        """Validate, persist, apply in-process, publish reload.

        Stage 2: also write an audit row capturing the previous value
        (read BEFORE the upsert) and the new value. The audit write
        shares this method's transaction so the two rows commit or
        roll back together.

        Returns the coerced value as it was written.
        """
        coerced = validate_runtime_setting(key, value)
        # Stage 2: capture the previous value before mutating. The
        # "previous" we record is the override that was in place
        # (or ``None`` if the field was at the env default).
        prev_value = await self._repo.get_one(key)
        await self._repo.upsert(key, coerced)
        # Apply to in-process Settings + side effects BEFORE we
        # commit, so a side-effect that raises rolls back the row.
        setattr(self._settings, key, coerced)
        _apply_side_effects(self._settings, key)
        # Stage 2: append the audit row inside the same transaction.
        await self._audit.append(
            key=key,
            prev_value=prev_value,
            next_value=coerced,
            set_by_user_id=set_by_user_id,
        )
        await self._session.commit()
        await self._publish_reload()
        log.info(
            "settings.override_set",
            key=key,
            impact=RUNTIME_EDITABLE_BY_KEY[key].impact,
        )
        return coerced

    async def clear_override(
        self,
        key: str,
        *,
        set_by_user_id: str | None = None,
    ) -> bool:
        """Delete the override (revert to env default). Returns True
        if a row was removed, False if there was nothing to revert.

        Stage 2: when a row is actually removed, write an audit row
        with ``next_value=None`` to record the clear. Idempotent
        no-op calls (clearing a field that was already at default)
        do NOT write an audit row — they didn't change anything.
        """
        if key not in RUNTIME_EDITABLE_BY_KEY:
            raise RuntimeSettingValidationError(
                f"{key!r} is not a runtime-editable setting."
            )
        # Stage 2: capture the previous override value before delete.
        prev_value = await self._repo.get_one(key)
        removed = await self._repo.delete(key)
        if removed:
            # Restore the env default value in-process. We resolve
            # the default from Settings's class definition rather
            # than from the schema entry, so users who set the var
            # in their env file get THEIR default, not ours.
            spec = RUNTIME_EDITABLE_BY_KEY[key]
            env_default = Settings.model_fields[key].default
            if env_default is None:
                env_default = spec.field_default
            setattr(self._settings, key, env_default)
            _apply_side_effects(self._settings, key)
            # Stage 2: append the audit row inside the same
            # transaction.  ``next_value=None`` signals "cleared
            # back to env default" — the reader can resolve the
            # effective value at that time by looking at the field's
            # env default.
            await self._audit.append(
                key=key,
                prev_value=prev_value,
                next_value=None,
                set_by_user_id=set_by_user_id,
            )
            await self._session.commit()
            await self._publish_reload()
            log.info("settings.override_cleared", key=key)
        return removed

    async def list_history(
        self, key: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Stage 2: return the recent change log for ``key``, newest
        first.

        Rejects unknown keys with a validation error so a typo doesn't
        silently return an empty list (which would otherwise be
        indistinguishable from "no changes have ever been made to
        this real field"). Returns a list of plain dicts so the API
        layer doesn't have to re-serialize ORM rows."""
        if key not in RUNTIME_EDITABLE_BY_KEY:
            raise RuntimeSettingValidationError(
                f"{key!r} is not a runtime-editable setting."
            )
        rows = await self._audit.list_for_key(key, limit=limit)
        return [
            {
                "id": row.id,
                "key": row.key,
                "prev_value": row.prev_value,
                "next_value": row.next_value,
                "set_by_user_id": row.set_by_user_id,
                "set_at": row.set_at.isoformat(),
            }
            for row in rows
        ]

    async def _publish_reload(self) -> None:
        """Tell every subscriber to reload. Best-effort — a Redis
        outage shouldn't fail the write (the in-process change has
        already been applied)."""
        try:
            from app.storage.cache import get_redis

            redis_client = get_redis().client
            await redis_client.publish(
                RELOAD_CHANNEL,
                json.dumps({"reason": "settings.changed"}),
            )
        except Exception as exc:  # noqa: BLE001 — broad on purpose
            log.warning(
                "settings.reload_publish_failed",
                error=str(exc),
                hint="In-process change applied; other workers will "
                "see it after restart.",
            )


# ── Subscriber ────────────────────────────────────────────────
async def reload_listener(settings: Settings) -> None:
    """Background task: subscribe to ``RELOAD_CHANNEL`` and re-apply
    overrides on every notification.

    Started by both the API lifespan and the worker startup hook so
    every process picks up settings changes initiated from anywhere.

    Cancellation-safe — if the task is cancelled mid-message we
    leave cleanly.
    """
    from app.storage.cache import get_redis
    from app.storage.database import get_database

    db = get_database()
    redis_client = get_redis().client
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(RELOAD_CHANNEL)
    log.info("settings.reload_listener_started", channel=RELOAD_CHANNEL)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                async with db.session() as session:
                    await load_and_apply_overrides(session, settings)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "settings.reload_failed", error=str(exc)
                )
    except asyncio.CancelledError:
        log.info("settings.reload_listener_stopping")
        await pubsub.unsubscribe(RELOAD_CHANNEL)
        await pubsub.aclose()
        raise


# ── Secret service ────────────────────────────────────────────
class SecretService:
    """Façade for the encrypted_secrets table.

    Reads return metadata only (``has_value``, ``last_set_at``,
    ``last_tested_at``, ``last_test_ok``). The plaintext leaves the
    service only via :meth:`get_plaintext`, intended for in-process
    callers (e.g. the VirusTotal client).
    """

    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session
        self._repo = SecretRepository(session)
        self._box = get_secret_box()

    async def list_status(self) -> list[dict[str, Any]]:
        """One status entry per managed secret slot, whether or not a
        value has been set. UI uses this to render the editor.
        """
        rows = await self._repo.list_metadata()
        by_key = {r.key: r for r in rows}
        out: list[dict[str, Any]] = []
        for key, spec in SECRETS_BY_KEY.items():
            row = by_key.get(key)
            out.append(
                {
                    "key": key,
                    "label": spec.label,
                    "category": spec.category,
                    "has_value": row is not None,
                    "last_set_at": row.last_set_at.isoformat() if row else None,
                    "set_by_user_id": row.set_by_user_id if row else None,
                    "last_tested_at": (
                        row.last_tested_at.isoformat()
                        if row and row.last_tested_at
                        else None
                    ),
                    "last_test_ok": row.last_test_ok if row else None,
                    "last_test_detail": (
                        row.last_test_detail if row else None
                    ),
                }
            )
        return out

    async def set_secret(
        self, *, key: str, plaintext: str, set_by_user_id: str | None
    ) -> None:
        """Validate the plaintext, encrypt, upsert. Commits the
        transaction itself so a failed encrypt doesn't leave a partial
        write."""
        validate_secret(key, plaintext)
        ciphertext = self._box.encrypt_bytes(plaintext.encode("utf-8"))
        await self._repo.upsert(
            key=key,
            ciphertext=ciphertext,
            set_by_user_id=set_by_user_id,
        )
        await self._session.commit()
        log.info("secrets.set", key=key, user_id=set_by_user_id)

    async def clear_secret(self, key: str) -> bool:
        if key not in SECRETS_BY_KEY:
            raise RuntimeSettingValidationError(
                f"{key!r} is not a managed secret."
            )
        removed = await self._repo.delete(key)
        if removed:
            await self._session.commit()
            log.info("secrets.cleared", key=key)
        return removed

    async def get_plaintext(self, key: str) -> str | None:
        """In-process accessor for service code that actually needs
        the secret. Returns None if the secret is not set.

        Never expose this over HTTP. Callers should pull the secret
        immediately before use and not hold onto it.
        """
        row = await self._repo.get_one(key)
        if row is None:
            return None
        return self._box.decrypt_bytes(row.ciphertext).decode("utf-8")

    async def record_test_outcome(
        self, *, key: str, ok: bool, detail: str | None
    ) -> None:
        """Used by the test endpoint after probing the upstream API."""
        await self._repo.record_test_outcome(key=key, ok=ok, detail=detail)
        await self._session.commit()
