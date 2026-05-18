"""v1.9 Stage 4.4 — rule_templates

Revision ID: 0028_stage4_4_rule_templates
Revises: 0027_stage17_playback_sessions
Create Date: 2026-05-17 21:40:00

Per v1.9 Stage 4.4: built-in rules become templates the operator
can copy ("Use template" → creates a normal Rule row). Templates
do NOT evaluate against media themselves; they're reference
material in a new table.

This migration is purely additive: the new table is empty after
``upgrade()``; the seed pass that populates it runs on every app
startup (see ``app.rules.builtin.register_builtin_templates``).
Existing ``rules`` rows are untouched — operators don't lose
anything they relied on.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Alembic identifiers.
revision = "0028_stage4_4_rule_templates"
down_revision = "0027_stage17_playback_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rule_templates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column(
            "seeded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_rule_templates_name", "rule_templates", ["name"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_rule_templates_name", "rule_templates")
    op.drop_table("rule_templates")
