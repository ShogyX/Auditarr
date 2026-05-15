"""media core

Revision ID: 0002_media_core
Revises: 0001_initial_auth
Create Date: 2026-05-10 19:30:00

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_media_core"
down_revision: str | None = "0001_initial_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── libraries ────────────────────────────────────────────────
    op.create_table(
        "libraries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("root_path", sa.String(length=1024), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("integration_link", sa.JSON(), nullable=True),
        sa.Column("scan_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("last_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_scan_status", sa.String(length=16), nullable=True),
        sa.Column("last_scan_file_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_libraries"),
        sa.UniqueConstraint("name", name="uq_libraries_name"),
    )
    op.create_index("ix_libraries_name", "libraries", ["name"], unique=False)

    # ── media_files ─────────────────────────────────────────────
    op.create_table(
        "media_files",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("library_id", sa.String(length=36), nullable=False),
        sa.Column("path", sa.String(length=2048), nullable=False),
        sa.Column("relative_path", sa.String(length=2048), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("extension", sa.String(length=16), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mtime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("inode", sa.BigInteger(), nullable=True),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("severity_rank", sa.Integer(), nullable=False),
        sa.Column("container", sa.String(length=32), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("bitrate_kbps", sa.Integer(), nullable=True),
        sa.Column("video_codec", sa.String(length=32), nullable=True),
        sa.Column("audio_codec", sa.String(length=32), nullable=True),
        sa.Column("subtitle_codec", sa.String(length=32), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("framerate", sa.Float(), nullable=True),
        sa.Column("has_subtitles", sa.Boolean(), nullable=False),
        sa.Column("subtitle_languages", sa.JSON(), nullable=True),
        sa.Column("audio_languages", sa.JSON(), nullable=True),
        sa.Column("probe", sa.JSON(), nullable=True),
        sa.Column("probe_failed", sa.Boolean(), nullable=False),
        sa.Column("probe_error", sa.String(length=512), nullable=True),
        sa.Column("last_scan_id", sa.String(length=36), nullable=True),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_orphaned", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["library_id"],
            ["libraries.id"],
            name="fk_media_files_library_id_libraries",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_media_files"),
        sa.UniqueConstraint("path", name="uq_media_files_path"),
    )
    op.create_index("ix_media_files_library_id", "media_files", ["library_id"], unique=False)
    op.create_index("ix_media_files_path", "media_files", ["path"], unique=False)
    op.create_index("ix_media_files_extension", "media_files", ["extension"], unique=False)
    op.create_index("ix_media_files_severity", "media_files", ["severity"], unique=False)
    op.create_index("ix_media_files_video_codec", "media_files", ["video_codec"], unique=False)
    op.create_index("ix_media_files_last_scan_id", "media_files", ["last_scan_id"], unique=False)
    op.create_index("ix_media_files_is_orphaned", "media_files", ["is_orphaned"], unique=False)
    op.create_index(
        "ix_media_files_library_category",
        "media_files",
        ["library_id", "category"],
        unique=False,
    )
    op.create_index(
        "ix_media_files_library_severity",
        "media_files",
        ["library_id", "severity"],
        unique=False,
    )

    # ── media_tags ──────────────────────────────────────────────
    op.create_table(
        "media_tags",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("media_file_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["media_file_id"],
            ["media_files.id"],
            name="fk_media_tags_media_file_id_media_files",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_media_tags"),
        sa.UniqueConstraint(
            "media_file_id", "name", "source", name="uq_media_tags_file_name_source"
        ),
    )
    op.create_index(
        "ix_media_tags_media_file_id", "media_tags", ["media_file_id"], unique=False
    )
    op.create_index("ix_media_tags_name", "media_tags", ["name"], unique=False)
    op.create_index("ix_media_tags_source", "media_tags", ["source"], unique=False)

    # ── scan_runs ───────────────────────────────────────────────
    op.create_table(
        "scan_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("library_id", sa.String(length=36), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("files_seen", sa.Integer(), nullable=False),
        sa.Column("files_added", sa.Integer(), nullable=False),
        sa.Column("files_updated", sa.Integer(), nullable=False),
        sa.Column("files_orphaned", sa.Integer(), nullable=False),
        sa.Column("probe_failures", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(length=2048), nullable=True),
        sa.Column("options", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["library_id"],
            ["libraries.id"],
            name="fk_scan_runs_library_id_libraries",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scan_runs"),
    )
    op.create_index("ix_scan_runs_library_id", "scan_runs", ["library_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_scan_runs_library_id", table_name="scan_runs")
    op.drop_table("scan_runs")

    op.drop_index("ix_media_tags_source", table_name="media_tags")
    op.drop_index("ix_media_tags_name", table_name="media_tags")
    op.drop_index("ix_media_tags_media_file_id", table_name="media_tags")
    op.drop_table("media_tags")

    op.drop_index("ix_media_files_library_severity", table_name="media_files")
    op.drop_index("ix_media_files_library_category", table_name="media_files")
    op.drop_index("ix_media_files_is_orphaned", table_name="media_files")
    op.drop_index("ix_media_files_last_scan_id", table_name="media_files")
    op.drop_index("ix_media_files_video_codec", table_name="media_files")
    op.drop_index("ix_media_files_severity", table_name="media_files")
    op.drop_index("ix_media_files_extension", table_name="media_files")
    op.drop_index("ix_media_files_path", table_name="media_files")
    op.drop_index("ix_media_files_library_id", table_name="media_files")
    op.drop_table("media_files")

    op.drop_index("ix_libraries_name", table_name="libraries")
    op.drop_table("libraries")
