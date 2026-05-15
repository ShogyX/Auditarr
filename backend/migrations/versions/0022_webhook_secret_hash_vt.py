"""Stage 19: webhook secret + file hash + VirusTotal result columns.

Revision ID: 0022_webhook_secret_hash_vt
Revises: 0021_discovered_paths
Create Date: 2026-05-15 00:00:00

Adds five nullable columns:

* ``integrations.webhook_secret_ciphertext`` — encrypted shared
  secret for webhook HMAC verification. ``NULL`` = no secret set
  (incoming webhooks 401).
* ``media_files.hash_sha256`` — content SHA-256 hex (64 chars).
  Indexed because VirusTotal looks up by hash and the Files page
  may surface a "files with this hash" link in a future stage.
* ``media_files.hash_computed_at`` — when the hash was computed.
  Used together with ``mtime`` to decide whether to re-hash.
* ``media_files.virustotal_result`` — small JSON describing the
  most recent VT lookup result.
* ``media_files.virustotal_checked_at`` — when the lookup ran.

All columns nullable on purpose so the migration is reversible
and existing rows stay untouched.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_webhook_secret_hash_vt"
down_revision: str | None = "0021_discovered_paths"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "integrations",
        sa.Column("webhook_secret_ciphertext", sa.Text(), nullable=True),
    )
    op.add_column(
        "media_files",
        sa.Column("hash_sha256", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_media_files_hash_sha256",
        "media_files",
        ["hash_sha256"],
    )
    op.add_column(
        "media_files",
        sa.Column("hash_computed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "media_files",
        sa.Column("virustotal_result", sa.JSON(), nullable=True),
    )
    op.add_column(
        "media_files",
        sa.Column(
            "virustotal_checked_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("media_files", "virustotal_checked_at")
    op.drop_column("media_files", "virustotal_result")
    op.drop_column("media_files", "hash_computed_at")
    op.drop_index("ix_media_files_hash_sha256", table_name="media_files")
    op.drop_column("media_files", "hash_sha256")
    op.drop_column("integrations", "webhook_secret_ciphertext")
