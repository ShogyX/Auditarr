"""Runtime settings overrides + encrypted secrets (Stage 21).

Two single-key tables. Rows exist only when a value has been
customized; absent rows mean "use the env-driven default".

Why two tables:

* ``runtime_setting_overrides`` holds plain JSON values (ints,
  strings, bools, lists). The read endpoint returns the value
  verbatim so the UI can render it.
* ``encrypted_secrets`` holds Fernet-encrypted blobs for things
  like API keys. The read endpoint returns metadata only —
  ``{has_value, last_set_at, last_tested_at, last_test_ok}`` —
  never the plaintext. Plaintext is recovered only by the in-process
  service code that needs to actually use the secret.

Validation for both lives in :mod:`app.core.runtime_settings_schema`
rather than on the column itself, because the schema evolves with
the application and writing a migration for each new editable field
would be churn.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.base import Base, TimestampMixin


class RuntimeSettingOverride(Base, TimestampMixin):
    """One operator-customized value for a runtime-editable setting.

    The ``key`` matches an entry in
    :data:`app.core.runtime_settings_schema.RUNTIME_EDITABLE_BY_KEY`.
    Writes are gated by the API layer through ``validate_runtime_
    setting`` so the column can stay schemaless without losing
    safety.
    """

    __tablename__ = "runtime_setting_overrides"

    key: Mapped[str] = mapped_column(
        String(64), primary_key=True, nullable=False
    )
    value: Mapped[Any] = mapped_column(JSON, nullable=False)


class EncryptedSecret(Base, TimestampMixin):
    """Fernet-encrypted secret. Plaintext is never returned over HTTP.

    The Fernet key is derived from ``Settings.secret_key`` at startup
    (see :mod:`app.security.secret_box`). Rotating ``secret_key``
    invalidates every secret in this table — operators rotating must
    also re-enter the affected secrets after the rotation.
    """

    __tablename__ = "encrypted_secrets"

    key: Mapped[str] = mapped_column(
        String(64), primary_key=True, nullable=False
    )
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    set_by_user_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    last_set_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Optional outcome of the most recent ``POST /secrets/{key}/test``.
    # Lets the UI show "API key works" / "rate-limited" / "401 unauth"
    # without exposing the secret itself.
    last_tested_at: Mapped[_dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_detail: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )


class RuntimeSettingChange(Base):
    """Append-only audit log for runtime-setting override changes (Stage 2).

    Every call to ``RuntimeSettingsService.set_override`` or
    ``clear_override`` writes one row here. The row captures the
    operator who made the change, the previous value (``None`` if it
    was the env default), the new value (``None`` for a clear), and
    the time of the change.

    Read access is admin-only via ``GET /system/runtime-settings/{key}/history``.
    No update or delete API exists — this table is append-only by
    design. Operators who want to "undo" use the same API to write
    another change that restores the previous value; the trail
    remains.

    Retention: today there is no retention policy. The expectation is
    that runtime-setting changes are infrequent (operator-driven,
    not user-driven) so this table won't grow without bound.
    """

    __tablename__ = "runtime_setting_changes"

    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True
    )
    key: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    prev_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    next_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    set_by_user_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    set_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
