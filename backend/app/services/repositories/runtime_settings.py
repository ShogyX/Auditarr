"""Repositories for runtime settings overrides and encrypted secrets
(Stage 21).

Both tables are single-key (no ``id`` column, primary key IS the
setting name). The repositories therefore expose ``get_one`` /
``upsert`` / ``delete`` rather than the typical list/add/remove
quartet.

Stage 2 adds :class:`RuntimeSettingChangeRepository` — an append-only
audit log written by every override change. It is NOT a single-key
repo (multiple rows per key over time).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.runtime_setting import (
    EncryptedSecret,
    RuntimeSettingChange,
    RuntimeSettingOverride,
)
from app.utils.datetime import utcnow


class RuntimeOverrideRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> dict[str, Any]:
        """Return ``{key: value}`` for every persisted override.

        Used at startup to apply overrides to the in-process Settings
        instance, and by the read endpoint to surface the delta.
        """
        rows = (
            await self._session.execute(select(RuntimeSettingOverride))
        ).scalars().all()
        return {row.key: row.value for row in rows}

    async def get_one(self, key: str) -> Any:
        """Return the override value for ``key`` or ``None`` if absent."""
        row = await self._session.get(RuntimeSettingOverride, key)
        return row.value if row is not None else None

    async def upsert(self, key: str, value: Any) -> RuntimeSettingOverride:
        """Insert or update. Caller is responsible for validation
        before this is called — the repository trusts its inputs."""
        row = await self._session.get(RuntimeSettingOverride, key)
        if row is None:
            row = RuntimeSettingOverride(key=key, value=value)
            self._session.add(row)
        else:
            row.value = value
            row.updated_at = utcnow()
        await self._session.flush([row])
        return row

    async def delete(self, key: str) -> bool:
        """Remove the override for ``key``. Returns True if a row was
        deleted, False if there was nothing to delete (idempotent)."""
        result = await self._session.execute(
            delete(RuntimeSettingOverride).where(
                RuntimeSettingOverride.key == key
            )
        )
        return result.rowcount > 0


class SecretRepository:
    """Persistence for :class:`EncryptedSecret`.

    The repository hands ciphertext bytes in and out. Encryption /
    decryption is the caller's responsibility — see
    :class:`app.services.runtime_settings.SecretService`. Keeping the
    boundary here means the repo is testable without a working
    crypto stack.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_metadata(self) -> list[EncryptedSecret]:
        """Return every secret row WITHOUT decrypting. Used by the
        read endpoint to surface ``has_value`` flags."""
        return list(
            (await self._session.execute(select(EncryptedSecret)))
            .scalars()
            .all()
        )

    async def get_one(self, key: str) -> EncryptedSecret | None:
        return await self._session.get(EncryptedSecret, key)

    async def upsert(
        self,
        *,
        key: str,
        ciphertext: bytes,
        set_by_user_id: str | None,
    ) -> EncryptedSecret:
        row = await self._session.get(EncryptedSecret, key)
        now = utcnow()
        if row is None:
            row = EncryptedSecret(
                key=key,
                ciphertext=ciphertext,
                set_by_user_id=set_by_user_id,
                last_set_at=now,
            )
            self._session.add(row)
        else:
            row.ciphertext = ciphertext
            row.set_by_user_id = set_by_user_id
            row.last_set_at = now
            row.updated_at = now
            # Clear stale test result — the new ciphertext hasn't
            # been tested yet, and the old result no longer applies.
            row.last_tested_at = None
            row.last_test_ok = None
            row.last_test_detail = None
        await self._session.flush([row])
        return row

    async def delete(self, key: str) -> bool:
        result = await self._session.execute(
            delete(EncryptedSecret).where(EncryptedSecret.key == key)
        )
        return result.rowcount > 0

    async def record_test_outcome(
        self, *, key: str, ok: bool, detail: str | None
    ) -> EncryptedSecret | None:
        """Persist the most recent test outcome for ``key``. The UI
        uses these fields to show "API key works" / "API key 401"
        without needing access to the plaintext."""
        row = await self._session.get(EncryptedSecret, key)
        if row is None:
            return None
        row.last_tested_at = utcnow()
        row.last_test_ok = ok
        row.last_test_detail = detail[:512] if detail else None
        row.updated_at = utcnow()
        await self._session.flush([row])
        return row


class RuntimeSettingChangeRepository:
    """Append-only audit log for runtime-setting overrides (Stage 2).

    Two operations:

    * :meth:`append` — write one row recording a change. Called from
      inside :class:`RuntimeSettingsService.set_override` /
      ``clear_override`` so the audit row and the override row land
      in the same transaction; either both or neither persist.
    * :meth:`list_for_key` — read recent changes for a single key,
      newest first, capped by ``limit``. Powers the per-field history
      drawer in the Stage 2 Settings UI.

    Deliberately no update / delete / list-all surface. The audit
    log is append-only by design; bulk reads aren't a use case today
    (operators care about "what changed on THIS field recently?",
    not "show me every change ever").
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        *,
        key: str,
        prev_value: Any,
        next_value: Any,
        set_by_user_id: str | None,
    ) -> RuntimeSettingChange:
        """Write one audit row. Does NOT commit — the caller owns the
        transaction so this stays atomic with the override write."""
        row = RuntimeSettingChange(
            key=key,
            prev_value=prev_value,
            next_value=next_value,
            set_by_user_id=set_by_user_id,
            set_at=utcnow(),
        )
        self._session.add(row)
        await self._session.flush([row])
        return row

    async def list_for_key(
        self, key: str, *, limit: int = 50
    ) -> list[RuntimeSettingChange]:
        """Return up to ``limit`` recent changes for ``key`` (newest
        first). ``limit`` is clamped to [1, 500] so a malicious
        ``?limit=`` query can't pull the entire table in one request."""
        bounded = max(1, min(500, limit))
        result = await self._session.execute(
            select(RuntimeSettingChange)
            .where(RuntimeSettingChange.key == key)
            .order_by(desc(RuntimeSettingChange.set_at))
            .limit(bounded)
        )
        return list(result.scalars().all())
