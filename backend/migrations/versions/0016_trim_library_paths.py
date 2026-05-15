"""trim library name and root_path

Revision ID: 0016_trim_library_paths
Revises: 0015_runtime_setting_changes
Create Date: 2026-05-13 23:30:00

Strips leading/trailing whitespace from ``libraries.name`` and
``libraries.root_path`` for existing rows. A library row was found
in production with the value ``" /media/NAS-Pool/media/AnimeMovies"``
(leading space) which broke the scanner with ``FileNotFoundError``.

The schema-layer fix (``LibraryCreate`` / ``LibraryUpdate`` now run
a strip validator) prevents new rows from carrying stray whitespace,
but rows already on disk need a one-shot cleanup. That's this
migration.

Idempotent — re-running the upgrade after it's already been
applied is a no-op because TRIM-then-store leaves nothing further
to trim. Downgrade is also a no-op because there's no reversible
information to restore (the original whitespace is lost on
purpose).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016_trim_library_paths"
down_revision: str | None = "0015_runtime_setting_changes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ``TRIM(string, chars)`` (positional function-call form) works
    # on both SQLite and Postgres. The original draft used Postgres'
    # ``TRIM(BOTH E'...' FROM x)`` SQL-standard form, which SQLite
    # doesn't recognize at all (parse error on ``E``, plus SQLite's
    # ``TRIM`` doesn't accept the ``BOTH ... FROM`` syntax even
    # without the escape prefix). The positional form is documented
    # for both engines and produces identical semantics.
    chars = " \t\n\r"  # space, tab, newline, carriage return
    # None of these chars are single-quotes, so the bare ``'...'``
    # SQL literal is unambiguous.
    op.execute(
        f"""
        UPDATE libraries
        SET name = TRIM(name, '{chars}')
        WHERE name <> TRIM(name, '{chars}')
        """
    )
    op.execute(
        f"""
        UPDATE libraries
        SET root_path = TRIM(root_path, '{chars}')
        WHERE root_path <> TRIM(root_path, '{chars}')
        """
    )


def downgrade() -> None:
    # Cannot recover the original whitespace; the trim is destructive
    # on purpose. No-op.
    pass
