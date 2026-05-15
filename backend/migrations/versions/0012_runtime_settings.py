"""runtime settings + secrets (Stage 21)

Revision ID: 0012_runtime_settings
Revises: 0011_rule_suggestions
Create Date: 2026-05-11 18:00:00

Adds two tables backing the runtime-editable Settings UI:

* ``runtime_setting_overrides`` — per-key JSON-valued overrides for
  the operational toggles whitelisted in
  ``app.core.runtime_settings_schema``. Rows exist only when an
  operator has customized a value; absent rows mean "use the
  env-driven default".

* ``encrypted_secrets`` — per-key Fernet-encrypted blobs for things
  like the VirusTotal API key. The read endpoint returns metadata
  only (has_value, last_set_at, set_by_user_id); the plaintext is
  never returned to the client.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_runtime_settings"
down_revision: str | None = "0011_rule_suggestions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runtime_setting_overrides",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "encrypted_secrets",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        # Audit fields — who set it and when. The plaintext is never
        # available for audit after-the-fact (by design).
        sa.Column(
            "set_by_user_id", sa.String(length=36), nullable=True
        ),
        sa.Column(
            "last_set_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "last_tested_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "last_test_ok", sa.Boolean(), nullable=True
        ),
        sa.Column(
            "last_test_detail", sa.String(length=512), nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("encrypted_secrets")
    op.drop_table("runtime_setting_overrides")
