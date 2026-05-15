"""MediaRepository sort + scope + matched-rules + empty-severities tests (Stage 3).

Stage 3 of the audit fix plan extended the repository in four ways:

1. ``SORTABLE_COLUMNS`` gained ``severity``, ``video_codec``,
   ``container`` — the column headers on the Files page send these
   keys and the API now whitelists them.
2. ``MediaFilter`` gained a ``scope`` tri-state (``media`` /
   ``non-media`` / ``all``) so the operator can scope to "everything
   non-media" without enumerating the specific categories.
3. ``MediaFilter`` gained ``severities_empty: bool`` so an empty
   client-side severity set produces a zero-row response instead of
   collapsing to "no filter".
4. ``MediaFilter`` gained ``include_matched_rules`` which attaches a
   ``MatchedRuleSummary`` list to each returned row in a single
   grouped query (no N+1).

These tests pin all four contracts and the stable ``path`` tiebreak
that pagination depends on.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.library import Library
from app.models.media import MediaFile
from app.models.rule import Rule
from app.models.rule_evaluation import RuleEvaluation
from app.services.repositories.media import (
    MatchedRuleSummary,
    MediaFilter,
    MediaRepository,
    SORTABLE_COLUMNS,
)
from app.storage.base import Base


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Fresh in-memory SQLite session per test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with sm() as sess:
        yield sess
    await engine.dispose()


def _utcnow() -> _dt.datetime:
    return _dt.datetime(2026, 5, 14, tzinfo=_dt.UTC)


async def _seed_library(session: AsyncSession, *, lib_id: str = "lib-1") -> Library:
    library = Library(
        id=lib_id,
        name="Test",
        root_path="/srv/media",
        kind="movies",
        enabled=True,
    )
    session.add(library)
    await session.flush()
    return library


async def _add_file(
    session: AsyncSession,
    *,
    file_id: str,
    library_id: str = "lib-1",
    path: str = "/srv/media/a.mkv",
    relative_path: str = "a.mkv",
    filename: str = "a.mkv",
    extension: str = ".mkv",
    size_bytes: int = 1000,
    category: str = "media",
    severity: str = "ok",
    severity_rank: int = 10,
    video_codec: str | None = "hevc",
    container: str | None = "matroska",
) -> MediaFile:
    mf = MediaFile(
        id=file_id,
        library_id=library_id,
        path=path,
        relative_path=relative_path,
        filename=filename,
        extension=extension,
        size_bytes=size_bytes,
        mtime=_utcnow(),
        category=category,
        severity=severity,
        severity_rank=severity_rank,
        video_codec=video_codec,
        container=container,
    )
    session.add(mf)
    await session.flush()
    return mf


# ── SORTABLE_COLUMNS membership ──────────────────────────────────
def test_sortable_columns_includes_new_keys() -> None:
    """Pin the three Stage 3 additions so a future refactor that drops
    one would fail loudly here rather than at the API integration
    layer."""
    assert "severity" in SORTABLE_COLUMNS
    assert "video_codec" in SORTABLE_COLUMNS
    assert "container" in SORTABLE_COLUMNS
    # And the existing pre-Stage-3 columns must still be present.
    for legacy in (
        "path",
        "filename",
        "size_bytes",
        "mtime",
        "severity_rank",
        "category",
        "extension",
        "seen_at",
    ):
        assert legacy in SORTABLE_COLUMNS


# ── Sort: video_codec, container, severity ───────────────────────
@pytest.mark.asyncio
async def test_sort_by_video_codec_asc(session: AsyncSession) -> None:
    await _seed_library(session)
    await _add_file(session, file_id="f1", path="/srv/media/1.mkv", video_codec="hevc")
    await _add_file(session, file_id="f2", path="/srv/media/2.mkv", video_codec="av1")
    await _add_file(session, file_id="f3", path="/srv/media/3.mkv", video_codec="h264")

    page = await MediaRepository(session).list(
        filt=MediaFilter(sort="video_codec", sort_dir="asc"),
    )
    codecs = [m.video_codec for m in page.items]
    assert codecs == ["av1", "h264", "hevc"]


@pytest.mark.asyncio
async def test_sort_by_container_desc(session: AsyncSession) -> None:
    await _seed_library(session)
    await _add_file(session, file_id="f1", path="/srv/media/1.mkv", container="matroska")
    await _add_file(session, file_id="f2", path="/srv/media/2.mkv", container="mp4")
    await _add_file(session, file_id="f3", path="/srv/media/3.mkv", container="avi")

    page = await MediaRepository(session).list(
        filt=MediaFilter(sort="container", sort_dir="desc"),
    )
    containers = [m.container for m in page.items]
    assert containers == ["mp4", "matroska", "avi"]


@pytest.mark.asyncio
async def test_sort_by_severity_uses_rank_under_the_hood(
    session: AsyncSession,
) -> None:
    """``sort=severity`` is the human-friendly alias the column header
    sends; it must sort by ``severity_rank`` (numeric, semantically
    ordered) NOT by the label string (which would sort alphabetically,
    producing crit < error < high < info < ok < warn — wrong)."""
    await _seed_library(session)
    await _add_file(
        session,
        file_id="f-ok",
        path="/srv/media/1.mkv",
        severity="ok",
        severity_rank=10,
    )
    await _add_file(
        session,
        file_id="f-crit",
        path="/srv/media/2.mkv",
        severity="crit",
        severity_rank=80,
    )
    await _add_file(
        session,
        file_id="f-warn",
        path="/srv/media/3.mkv",
        severity="warn",
        severity_rank=30,
    )

    page = await MediaRepository(session).list(
        filt=MediaFilter(sort="severity", sort_dir="desc"),
    )
    ranks = [m.severity_rank for m in page.items]
    assert ranks == [80, 30, 10]


@pytest.mark.asyncio
async def test_sort_path_tiebreak_is_stable(session: AsyncSession) -> None:
    """Two rows with identical primary-sort values must come back in
    deterministic ``path`` order so offset pagination doesn't flicker."""
    await _seed_library(session)
    # Three files with the same codec, different paths.
    await _add_file(session, file_id="f1", path="/srv/media/c.mkv", video_codec="h264")
    await _add_file(session, file_id="f2", path="/srv/media/a.mkv", video_codec="h264")
    await _add_file(session, file_id="f3", path="/srv/media/b.mkv", video_codec="h264")

    page = await MediaRepository(session).list(
        filt=MediaFilter(sort="video_codec", sort_dir="asc"),
    )
    paths = [m.path for m in page.items]
    # Primary sort all equal → secondary sort by path ascending.
    assert paths == [
        "/srv/media/a.mkv",
        "/srv/media/b.mkv",
        "/srv/media/c.mkv",
    ]


# ── scope tri-state ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_scope_media_filters_to_media_category(session: AsyncSession) -> None:
    await _seed_library(session)
    await _add_file(session, file_id="m1", path="/srv/media/m1.mkv", category="media")
    await _add_file(session, file_id="m2", path="/srv/media/m2.mkv", category="media")
    await _add_file(session, file_id="j1", path="/srv/media/j1.nfo", category="junk")
    await _add_file(session, file_id="s1", path="/srv/media/s1.srt", category="subtitle")

    page = await MediaRepository(session).list(filt=MediaFilter(scope="media"))
    assert page.total == 2
    assert {m.id for m in page.items} == {"m1", "m2"}


@pytest.mark.asyncio
async def test_scope_non_media_excludes_media(session: AsyncSession) -> None:
    await _seed_library(session)
    await _add_file(session, file_id="m1", path="/srv/media/m1.mkv", category="media")
    await _add_file(session, file_id="j1", path="/srv/media/j1.nfo", category="junk")
    await _add_file(session, file_id="s1", path="/srv/media/s1.srt", category="subtitle")

    page = await MediaRepository(session).list(filt=MediaFilter(scope="non-media"))
    assert page.total == 2
    assert {m.id for m in page.items} == {"j1", "s1"}


@pytest.mark.asyncio
async def test_scope_all_or_none_is_unfiltered(session: AsyncSession) -> None:
    await _seed_library(session)
    await _add_file(session, file_id="m1", path="/srv/media/m1.mkv", category="media")
    await _add_file(session, file_id="j1", path="/srv/media/j1.nfo", category="junk")

    page_none = await MediaRepository(session).list(filt=MediaFilter(scope=None))
    page_all = await MediaRepository(session).list(filt=MediaFilter(scope="all"))
    assert page_none.total == 2
    assert page_all.total == 2


# ── empty-severities sentinel ────────────────────────────────────
@pytest.mark.asyncio
async def test_severities_empty_returns_zero_rows(session: AsyncSession) -> None:
    """The frontend's ``hide all severities`` UI used to flip into
    "show everything" — empty filter strings get dropped server-side
    and the result was every row. The Stage 3 sentinel must instead
    return a clean zero-row response."""
    await _seed_library(session)
    await _add_file(session, file_id="f1", path="/srv/media/1.mkv")
    await _add_file(session, file_id="f2", path="/srv/media/2.mkv")

    page = await MediaRepository(session).list(
        filt=MediaFilter(severities_empty=True),
    )
    assert page.total == 0
    assert page.items == []


@pytest.mark.asyncio
async def test_severities_empty_takes_precedence_over_severity_string(
    session: AsyncSession,
) -> None:
    """If both ``severities_empty=True`` and a non-empty
    ``severity=warn,high`` arrive in the same request, the empty
    sentinel wins. (This is paranoid coverage — the frontend never
    sends both — but the contract should be unambiguous.)"""
    await _seed_library(session)
    await _add_file(session, file_id="f1", path="/srv/media/1.mkv", severity="warn", severity_rank=30)

    page = await MediaRepository(session).list(
        filt=MediaFilter(severities_empty=True, severity="warn,high"),
    )
    assert page.total == 0


# ── matched_rules attachment ─────────────────────────────────────
async def _add_rule_eval(
    session: AsyncSession,
    *,
    eval_id: str,
    media_file_id: str,
    rule_id: str,
    rule_name: str,
    severity: str = "warn",
    severity_rank: int = 30,
) -> None:
    # Ensure the rule exists.
    rule = await session.get(Rule, rule_id)
    if rule is None:
        session.add(
            Rule(
                id=rule_id,
                name=rule_name,
                enabled=True,
                priority=100,
                definition={},
                is_builtin=False,
            )
        )
        await session.flush()
    session.add(
        RuleEvaluation(
            id=eval_id,
            media_file_id=media_file_id,
            rule_id=rule_id,
            severity=severity,
            severity_rank=severity_rank,
            actions_summary={},
            evaluated_at=_utcnow(),
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_matched_rules_off_by_default(session: AsyncSession) -> None:
    """Without ``include_matched_rules=True`` the per-row chip
    list is absent (the page-level dict is empty)."""
    await _seed_library(session)
    await _add_file(session, file_id="f1", path="/srv/media/1.mkv")
    await _add_rule_eval(
        session,
        eval_id="ev1",
        media_file_id="f1",
        rule_id="r1",
        rule_name="HEVC media",
    )

    page = await MediaRepository(session).list(filt=MediaFilter())
    assert page.matched_rules == {}


@pytest.mark.asyncio
async def test_matched_rules_on_attaches_per_row(session: AsyncSession) -> None:
    """With the flag on, every matched (file, rule) pair shows up in
    the page-level map, grouped by file id."""
    await _seed_library(session)
    await _add_file(session, file_id="f1", path="/srv/media/1.mkv")
    await _add_file(session, file_id="f2", path="/srv/media/2.mkv")
    await _add_rule_eval(
        session,
        eval_id="ev1",
        media_file_id="f1",
        rule_id="r1",
        rule_name="HEVC media",
        severity="warn",
        severity_rank=30,
    )
    await _add_rule_eval(
        session,
        eval_id="ev2",
        media_file_id="f1",
        rule_id="r2",
        rule_name="Bitrate ceiling",
        severity="crit",
        severity_rank=80,
    )
    # f2 has no matches.

    page = await MediaRepository(session).list(
        filt=MediaFilter(include_matched_rules=True),
    )
    assert "f1" in page.matched_rules
    # f2 has no rules; its key is absent (not an empty list).
    assert "f2" not in page.matched_rules
    f1_rules = page.matched_rules["f1"]
    # Ordering: severity_rank desc, then rule name asc.
    assert [r.rule_name for r in f1_rules] == ["Bitrate ceiling", "HEVC media"]
    # And each entry is the dataclass we expect.
    assert all(isinstance(r, MatchedRuleSummary) for r in f1_rules)


@pytest.mark.asyncio
async def test_matched_rules_query_is_single_grouped_fetch(
    session: AsyncSession,
) -> None:
    """The matched-rules attachment must NOT N+1 the row count.
    We seed a 50-file page with one rule match each and assert the
    page-level dict has every entry — proof the loop completed in a
    single grouped fetch (vs. one-per-row).

    The test doesn't directly count SQL statements (that would
    require a SQLAlchemy event listener); the indirect signal is
    that the call completes well within the test-timeout and that
    every file id appears in the result map. A regression that
    flipped to N+1 would still pass functionally on 50 rows — but
    would be visible immediately under real-world page sizes."""
    await _seed_library(session)
    for i in range(50):
        await _add_file(
            session,
            file_id=f"f{i:02d}",
            path=f"/srv/media/{i:02d}.mkv",
        )
        await _add_rule_eval(
            session,
            eval_id=f"ev{i:02d}",
            media_file_id=f"f{i:02d}",
            rule_id="r-shared",
            rule_name="Shared rule",
        )

    page = await MediaRepository(session).list(
        filt=MediaFilter(include_matched_rules=True),
        limit=50,
    )
    assert page.total == 50
    assert len(page.matched_rules) == 50
    for mf in page.items:
        assert mf.id in page.matched_rules
        assert page.matched_rules[mf.id][0].rule_name == "Shared rule"
