"""v1.10 — Files filter expansion: tag include/exclude, rule
include/exclude, has_subtitles, resolution buckets.

Pins:
  1. ``tags_all`` requires every listed name (AND semantic).
  2. ``tags_none`` excludes files carrying any listed name.
  3. Combining ``tags_any`` + ``tags_all`` + ``tags_none`` gives the
     intersection.
  4. ``rules_any`` / ``rules_all`` / ``rules_none`` mirror the tag
     semantics but match on ``rule_evaluations.rule_id``.
  5. ``has_subtitles`` is a tri-state — None / True / False.
  6. ``resolution_bucket`` maps the ``height`` column to display
     labels (sd / 480p / 720p / 1080p / 1440p / 2160p / 8k / unknown).
"""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest
import pytest_asyncio

from app.models.library import Library
from app.models.media import MediaFile
from app.models.rule import Rule
from app.models.rule_evaluation import RuleEvaluation
from app.models.tag import MediaTag
from app.services.repositories.media import MediaFilter, MediaRepository
from app.storage.base import Base
from app.storage.database import get_database


@pytest_asyncio.fixture
async def db_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "v110.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv(
        "AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars"
    )
    from app.core.settings import get_settings

    get_settings.cache_clear()
    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001
    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with db.session() as sess:
            yield sess
    finally:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        get_settings.cache_clear()


async def _seed(sess) -> dict[str, str]:
    """Three files spanning tag / rule / subtitle / height variations.

    A: tags={hevc, audited},      rules={r_hevc, r_audit}, subs=True,  height=2160
    B: tags={hevc},               rules={r_hevc},          subs=False, height=1080
    C: tags={hide},               rules={},                subs=True,  height=720
    """
    now = _dt.datetime.now(_dt.UTC)
    lib = Library(name="L", root_path="/d", kind="movies")
    sess.add(lib)
    await sess.flush()

    r_hevc = Rule(
        name="HEVC flagger",
        definition={"match": {"field": "video_codec", "op": "eq", "value": "hevc"}, "actions": []},
        enabled=True,
    )
    r_audit = Rule(
        name="Audited",
        definition={"match": {"field": "video_codec", "op": "eq", "value": "h264"}, "actions": []},
        enabled=True,
    )
    sess.add_all([r_hevc, r_audit])
    await sess.flush()

    a = MediaFile(
        library_id=lib.id,
        path="/d/a.mkv",
        relative_path="a.mkv",
        filename="a.mkv",
        extension="mkv",
        size_bytes=1024,
        mtime=now,
        has_subtitles=True,
        height=2160,
    )
    b = MediaFile(
        library_id=lib.id,
        path="/d/b.mkv",
        relative_path="b.mkv",
        filename="b.mkv",
        extension="mkv",
        size_bytes=2048,
        mtime=now,
        has_subtitles=False,
        height=1080,
    )
    c = MediaFile(
        library_id=lib.id,
        path="/d/c.mkv",
        relative_path="c.mkv",
        filename="c.mkv",
        extension="mkv",
        size_bytes=4096,
        mtime=now,
        has_subtitles=True,
        height=720,
    )
    sess.add_all([a, b, c])
    await sess.flush()

    sess.add_all([
        MediaTag(media_file_id=a.id, name="hevc", source="rule"),
        MediaTag(media_file_id=a.id, name="audited", source="rule"),
        MediaTag(media_file_id=b.id, name="hevc", source="rule"),
        MediaTag(media_file_id=c.id, name="hide", source="manual"),
    ])
    sess.add_all([
        RuleEvaluation(
            media_file_id=a.id,
            rule_id=r_hevc.id,
            severity="warn",
            severity_rank=30,
            actions_summary={},
            evaluated_at=now,
        ),
        RuleEvaluation(
            media_file_id=a.id,
            rule_id=r_audit.id,
            severity="info",
            severity_rank=10,
            actions_summary={},
            evaluated_at=now,
        ),
        RuleEvaluation(
            media_file_id=b.id,
            rule_id=r_hevc.id,
            severity="warn",
            severity_rank=30,
            actions_summary={},
            evaluated_at=now,
        ),
    ])
    await sess.commit()
    return {
        "a": a.id,
        "b": b.id,
        "c": c.id,
        "r_hevc": r_hevc.id,
        "r_audit": r_audit.id,
    }


# ── tags ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tags_all_requires_every_listed_tag(db_session) -> None:
    ids = await _seed(db_session)
    repo = MediaRepository(db_session)
    page = await repo.list(
        filt=MediaFilter(tags_all=["hevc", "audited"]),
        offset=0,
        limit=100,
    )
    assert [m.id for m in page.items] == [ids["a"]]


@pytest.mark.asyncio
async def test_tags_none_excludes_files_carrying_any_listed(db_session) -> None:
    ids = await _seed(db_session)
    repo = MediaRepository(db_session)
    page = await repo.list(
        filt=MediaFilter(tags_none=["hide", "audited"]),
        offset=0,
        limit=100,
    )
    # Only B (hevc only) survives.
    assert {m.id for m in page.items} == {ids["b"]}


@pytest.mark.asyncio
async def test_tag_predicates_intersect(db_session) -> None:
    ids = await _seed(db_session)
    repo = MediaRepository(db_session)
    page = await repo.list(
        filt=MediaFilter(
            tags_any=["hevc"],
            tags_all=["hevc"],
            tags_none=["audited"],
        ),
        offset=0,
        limit=100,
    )
    # A is hevc + audited → excluded by tags_none. B is hevc only → kept.
    assert {m.id for m in page.items} == {ids["b"]}


@pytest.mark.asyncio
async def test_blank_tag_entries_are_dropped(db_session) -> None:
    """Trimmed-to-empty list entries should be a no-op, not match
    every file via ``IN ('')``."""
    ids = await _seed(db_session)
    repo = MediaRepository(db_session)
    page = await repo.list(
        filt=MediaFilter(tags_all=["", "  "]),
        offset=0,
        limit=100,
    )
    # Empty list after trim → no filter; all three rows back.
    assert {m.id for m in page.items} == {ids["a"], ids["b"], ids["c"]}


# ── rules ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rules_any_matches_files_with_at_least_one_listed_rule(
    db_session,
) -> None:
    ids = await _seed(db_session)
    repo = MediaRepository(db_session)
    page = await repo.list(
        filt=MediaFilter(rules_any=[ids["r_hevc"]]),
        offset=0,
        limit=100,
    )
    # A + B both matched r_hevc.
    assert {m.id for m in page.items} == {ids["a"], ids["b"]}


@pytest.mark.asyncio
async def test_rules_all_requires_every_listed_rule(db_session) -> None:
    ids = await _seed(db_session)
    repo = MediaRepository(db_session)
    page = await repo.list(
        filt=MediaFilter(rules_all=[ids["r_hevc"], ids["r_audit"]]),
        offset=0,
        limit=100,
    )
    assert [m.id for m in page.items] == [ids["a"]]


@pytest.mark.asyncio
async def test_rules_none_excludes_listed_rule_matches(db_session) -> None:
    ids = await _seed(db_session)
    repo = MediaRepository(db_session)
    page = await repo.list(
        filt=MediaFilter(rules_none=[ids["r_hevc"]]),
        offset=0,
        limit=100,
    )
    # Only C didn't match r_hevc.
    assert [m.id for m in page.items] == [ids["c"]]


# ── subtitles + resolution ──────────────────────────────────────


@pytest.mark.asyncio
async def test_has_subtitles_tri_state(db_session) -> None:
    ids = await _seed(db_session)
    repo = MediaRepository(db_session)
    only_with = await repo.list(
        filt=MediaFilter(has_subtitles=True), offset=0, limit=100,
    )
    only_without = await repo.list(
        filt=MediaFilter(has_subtitles=False), offset=0, limit=100,
    )
    no_filter = await repo.list(
        filt=MediaFilter(has_subtitles=None), offset=0, limit=100,
    )
    assert {m.id for m in only_with.items} == {ids["a"], ids["c"]}
    assert {m.id for m in only_without.items} == {ids["b"]}
    assert {m.id for m in no_filter.items} == {ids["a"], ids["b"], ids["c"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bucket,expected",
    [
        ("2160p", {"a"}),
        ("4k", {"a"}),  # alias
        ("1080p", {"b"}),
        ("720p", {"c"}),
        ("sd", set()),
        ("unknown", set()),
    ],
)
async def test_resolution_bucket(db_session, bucket: str, expected) -> None:
    ids = await _seed(db_session)
    repo = MediaRepository(db_session)
    page = await repo.list(
        filt=MediaFilter(resolution_bucket=bucket), offset=0, limit=100,
    )
    assert {m.id for m in page.items} == {ids[label] for label in expected}
