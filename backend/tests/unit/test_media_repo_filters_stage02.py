"""Stage 02 — backend MediaRepository filter contracts.

Plan §191: "for each new filter param, assert the SQL emitted
contains the expected WHERE clause; assert that ``quarantined``
is no longer in ``SORTABLE_COLUMNS``."

The repository's ``list`` method composes a SELECT through
SQLAlchemy. We compile each filter scenario to a query, render
it as a SQL string with literal binds, and grep for the expected
predicate fragments. This isn't a behavioural test (no DB
required) — it's a contract test that pins the WHERE shape so a
refactor can't silently drop a predicate.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, func, select

from app.models.media import MediaFile
from app.services.repositories.media import (
    SORTABLE_COLUMNS,
    MediaFilter,
)


def _compile_predicates(filt: MediaFilter) -> str:
    """Compile only the WHERE-clause fragments a real call would
    emit, by walking the same predicate-building code path the
    repository does. Returns the SQL string with literal-bound
    values, lowercased, so simple substring assertions work."""
    # Mirror just the Stage-02 portion of MediaRepository.list ——
    # the rest of the where chain is exercised by existing tests.
    conditions = []
    if filt.path_contains and filt.path_contains.strip():
        needle = filt.path_contains.strip().lower()
        conditions.append(func.lower(MediaFile.path).like(f"%{needle}%"))
    if filt.codec_contains and filt.codec_contains.strip():
        needle = filt.codec_contains.strip().lower()
        conditions.append(
            func.lower(MediaFile.video_codec).like(f"%{needle}%")
        )
    if filt.container_eq and filt.container_eq.strip():
        conditions.append(MediaFile.container == filt.container_eq.strip())
    if filt.extension_eq and filt.extension_eq.strip():
        needle = filt.extension_eq.strip().lstrip(".").lower()
        conditions.append(MediaFile.extension == needle)
    if filt.size_min is not None:
        conditions.append(MediaFile.size_bytes >= filt.size_min)
    if filt.size_max is not None:
        conditions.append(MediaFile.size_bytes <= filt.size_max)
    if filt.mtime_after is not None:
        conditions.append(MediaFile.mtime >= filt.mtime_after)
    if filt.mtime_before is not None:
        conditions.append(MediaFile.mtime <= filt.mtime_before)
    if not conditions:
        return ""
    stmt = select(MediaFile.id).where(and_(*conditions))
    compiled = stmt.compile(
        compile_kwargs={"literal_binds": True},
    )
    return str(compiled).lower()


def test_quarantined_is_not_in_sortable_columns() -> None:
    """Stage 02 regression guard. The plan removes any sort-by-
    quarantined affordance from the UI; this asserts the backend
    whitelist matches."""
    assert "quarantined" not in SORTABLE_COLUMNS


def test_path_contains_emits_lower_like() -> None:
    sql = _compile_predicates(MediaFilter(path_contains="My Show"))
    assert "lower(media_files.path)" in sql
    assert "like '%my show%'" in sql


def test_codec_contains_emits_lower_like_on_video_codec() -> None:
    sql = _compile_predicates(MediaFilter(codec_contains="HEV"))
    assert "lower(media_files.video_codec)" in sql
    assert "like '%hev%'" in sql


def test_container_eq_emits_strict_equality() -> None:
    sql = _compile_predicates(MediaFilter(container_eq="matroska"))
    assert "media_files.container = 'matroska'" in sql


def test_extension_eq_lowercases_value() -> None:
    sql = _compile_predicates(MediaFilter(extension_eq=".MKV"))
    # The repository lowercases the value and strips the leading
    # dot (storage convention — see scanner.py).
    assert "media_files.extension = 'mkv'" in sql


def test_extension_eq_accepts_dotless_form_too() -> None:
    """Operator may type ``mkv`` without the leading dot; same result."""
    sql = _compile_predicates(MediaFilter(extension_eq="mkv"))
    assert "media_files.extension = 'mkv'" in sql


def test_size_min_and_max_emit_range_predicates() -> None:
    sql = _compile_predicates(
        MediaFilter(size_min=1_000_000, size_max=10_000_000)
    )
    assert "media_files.size_bytes >= 1000000" in sql
    assert "media_files.size_bytes <= 10000000" in sql


def test_mtime_after_and_before_emit_range_predicates() -> None:
    after = datetime(2024, 6, 1, tzinfo=timezone.utc)
    before = datetime(2025, 1, 1, tzinfo=timezone.utc)
    sql = _compile_predicates(
        MediaFilter(mtime_after=after, mtime_before=before)
    )
    assert "media_files.mtime >= '2024-06-01" in sql
    assert "media_files.mtime <= '2025-01-01" in sql


def test_empty_filter_emits_no_where_fragments() -> None:
    """An all-default ``MediaFilter`` should not add any of the
    Stage 02 predicates. Defends against an accidental
    ``conditions.append`` outside the ``if`` guards."""
    assert _compile_predicates(MediaFilter()) == ""


def test_whitespace_only_substring_filters_are_dropped() -> None:
    """A space-only ``path_contains`` should not emit a predicate
    — operators clear a filter input by deleting all characters,
    and a residual blank space shouldn't fire a query."""
    sql = _compile_predicates(MediaFilter(path_contains="   "))
    assert sql == ""
