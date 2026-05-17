"""stage 12 v1.7 — must_change_password flags

Revision ID: 0026_stage12_must_change_pw
Revises: 0025_stage10_vt_queue
Create Date: 2026-05-16 21:00:00

Per Stage 12 plan §580-581:

  * Add ``password_reset_tokens.must_change_on_use`` so a reset
    token issued by the terminal-OTP path can carry the
    "force-password-change" flag without changing token shape.
  * Add ``users.must_change_password`` so the login flow can
    detect a user who consumed such a token and redirect them
    to the change-password screen before they can use the app.

Both columns default to FALSE so existing rows behave as
before. The migration is purely additive.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# Alembic identifiers.
revision = "0026_stage12_must_change_pw"
down_revision = "0025_stage10_vt_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``users.must_change_password`` — defaults to False so
    # existing accounts aren't forced into the reset flow.
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # ``password_reset_tokens.must_change_on_use`` — defaults
    # to False so tokens issued before this migration behave
    # exactly as before. The terminal-OTP path is the only
    # caller that sets True.
    op.add_column(
        "password_reset_tokens",
        sa.Column(
            "must_change_on_use",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("password_reset_tokens", "must_change_on_use")
    op.drop_column("users", "must_change_password")
