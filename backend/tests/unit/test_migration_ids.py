"""Migration revision-ID length guard (Stage 19 audit follow-up).

Alembic's default ``alembic_version`` table holds the current
revision in a ``varchar(32)`` column. SQLite (used by our test
suite) treats VARCHAR length as advisory and silently allows
oversized values; Postgres (production) enforces the limit
strictly and raises ``StringDataRightTruncationError`` mid-write,
leaving the migration state inconsistent.

The original ``0021_integration_discovered_paths`` revision ID
was 33 characters. It passed every test on SQLite, blew up on
real Postgres deploys, and left the database between revisions.
This test pins the contract going forward — any new migration
exceeding the limit fails CI before it ships.

The 32-char cap is the Alembic default. Operators who run
``alembic_version`` with a wider column (some shops do — there's
an ``-x version_num_length=N`` config flag) can ignore this test;
production Auditarr does not, so the cap applies.
"""
from __future__ import annotations

import re
from pathlib import Path

# Alembic default. If a downstream consumer widens
# ``alembic_version.version_num``, they can fork this constant —
# but we ship the conservative default because that's what every
# fresh install gets.
ALEMBIC_VERSION_COLUMN_LIMIT = 32

_REVISION_RE = re.compile(r'^revision:\s*str\s*=\s*"([^"]+)"', re.MULTILINE)


def _versions_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "migrations" / "versions"


def _all_revisions() -> list[tuple[str, str]]:
    """Returns ``[(filename, revision_id), ...]`` for every migration."""
    out: list[tuple[str, str]] = []
    for path in sorted(_versions_dir().glob("0*.py")):
        text = path.read_text()
        match = _REVISION_RE.search(text)
        if match is None:
            continue
        out.append((path.name, match.group(1)))
    return out


def test_every_revision_id_fits_alembic_default_column() -> None:
    """No revision ID may exceed Alembic's default version_num
    length. Triggers a hard failure that's easier to debug than
    the Postgres truncation error operators saw in production."""
    overlong = [
        (filename, rev, len(rev))
        for filename, rev in _all_revisions()
        if len(rev) > ALEMBIC_VERSION_COLUMN_LIMIT
    ]
    assert not overlong, (
        "Revision IDs exceeding Alembic's default 32-char "
        f"version_num limit will truncate mid-write on Postgres "
        f"(but pass silently on SQLite). Offenders: {overlong}"
    )


def test_revision_id_set_is_unique() -> None:
    """Sanity check: no two migrations share a revision ID."""
    revisions = [rev for _, rev in _all_revisions()]
    duplicates = sorted({r for r in revisions if revisions.count(r) > 1})
    assert not duplicates, f"Duplicate revision IDs: {duplicates}"


def test_revision_count_is_nonzero() -> None:
    """If this fails, the discovery glob broke — without it the
    other two tests would falsely pass on an empty list."""
    assert len(_all_revisions()) > 0, "no migrations found"
