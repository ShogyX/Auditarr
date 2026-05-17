"""Stage 09 (v1.7) — Playback count fix tests.

Plan §492:
    Insert 25 playback events with mixed resolved/unresolved;
    assert the recommendation card query returns 25, with
    resolved=N, unresolved=M breakdown.

Addendum A.7:
    When ``resolved < fetched`` the dashboard shows a "couldn't
    be matched to library files" hint. The data this hint reads
    comes from the analyzer's outcome — we pin the field
    semantics here so the frontend can rely on them.

This pins:
  * ``examined_events_total`` counts ALL playback events in
    the 30-day window, regardless of resolution.
  * ``examined_events_resolved`` matches the legacy
    ``examined_events`` field (resolved-only, what the
    analyzer iterates over).
  * ``examined_events_unresolved`` = total − resolved.
  * The bug-pattern scenario (25 unresolved playbacks reported
    as "0" before Stage 09) returns ``total=25, resolved=0,
    unresolved=25``.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from app.models.integration import Integration
from app.models.library import Library
from app.models.media import MediaFile
from app.models.playback import PlaybackEvent
from app.services.playback import PlaybackAnalyzer
from app.storage.base import Base
from app.storage.cache import get_redis
from app.storage.database import get_database
from app.utils.datetime import utcnow


@pytest_asyncio.fixture
async def env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple]:
    db_path = tmp_path / "count09.db"
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

    async with db.session() as session:
        lib = Library(
            name="Movies", root_path="/mnt/media/Movies", kind="movies"
        )
        session.add(lib)
        await session.flush()
        # A handful of MediaFiles so SOME playbacks can resolve.
        for i in range(5):
            session.add(
                MediaFile(
                    library_id=lib.id,
                    path=f"/mnt/media/Movies/resolved-{i}.mkv",
                    relative_path=f"resolved-{i}.mkv",
                    filename=f"resolved-{i}.mkv",
                    extension="mkv",
                    size_bytes=1024 * 1024,
                    mtime=_dt.datetime.now(_dt.UTC),
                    category="media",
                    severity="ok",
                    severity_rank=10,
                    has_subtitles=False,
                    seen_at=_dt.datetime.now(_dt.UTC),
                    is_orphaned=False,
                )
            )
        integration = Integration(
            name="Stub",
            kind="stubplex",
            enabled=True,
            poll_interval_seconds=900,
            config={},
            health_status="unknown",
        )
        session.add(integration)
        await session.commit()
        # Map the seeded media paths to their IDs so the
        # per-test seeders can grab them by index.
        resolved_paths = [
            f"/mnt/media/Movies/resolved-{i}.mkv" for i in range(5)
        ]
        media_id_by_path: dict[str, str] = {}
        from sqlalchemy import select as _select

        rows = (
            await session.execute(_select(MediaFile))
        ).scalars().all()
        for r in rows:
            media_id_by_path[r.path] = r.id

    try:
        yield db, integration.id, resolved_paths, media_id_by_path
    finally:
        async with db.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await db.disconnect()
        try:
            await get_redis().disconnect()
        except Exception:  # noqa: BLE001
            pass
        get_settings.cache_clear()


async def _seed_playback_events(
    db,
    *,
    integration_id: str,
    resolved_paths: list[str],
    media_id_by_path: dict[str, str],
    resolved_count: int,
    unresolved_count: int,
    age_days: int = 1,
    tag: str = "",
) -> None:
    """Insert ``resolved_count`` events that link to MediaFiles
    + ``unresolved_count`` events with ``media_file_id=None``,
    all within the window starting ``age_days`` ago. ``tag`` is
    folded into ``upstream_id`` so two calls with the same
    resolved/unresolved counts produce non-colliding upstream
    ids."""
    started = utcnow() - _dt.timedelta(days=age_days)
    async with db.session() as session:
        upstream_n = 0
        # Resolved batch — pick from the seeded paths (round-
        # robin if we need more than 5).
        for i in range(resolved_count):
            path = resolved_paths[i % len(resolved_paths)]
            session.add(
                PlaybackEvent(
                    integration_id=integration_id,
                    media_file_id=media_id_by_path[path],
                    source_path=path,
                    decision="direct_play",
                    started_at=started + _dt.timedelta(seconds=i),
                    upstream_id=f"resolved-{tag}-{upstream_n}",
                )
            )
            upstream_n += 1
        # Unresolved batch — paths that don't match any
        # MediaFile; media_file_id stays None.
        for i in range(unresolved_count):
            session.add(
                PlaybackEvent(
                    integration_id=integration_id,
                    media_file_id=None,
                    source_path=f"/wrong/path/u-{i}.mkv",
                    decision="direct_play",
                    started_at=started + _dt.timedelta(seconds=i + 1000),
                    upstream_id=f"unresolved-{tag}-{upstream_n}",
                )
            )
            upstream_n += 1
        await session.commit()


# ── Test 1 — Plan §492 + bug-pattern scenario ──────────────────


@pytest.mark.asyncio
async def test_count_query_reports_25_events_when_25_actually_happened(
    env,
) -> None:
    """Plan §492 + bug-pattern scenario reported by the user:
    "0 playbacks the last 30 days when over 20 have actually
    happened."

    Insert 25 unresolved playbacks (path mappings broken so
    none of them link to a MediaFile). Before Stage 09 the
    analyzer reported ``examined_events=0``. After Stage 09
    ``examined_events_total=25`` and the unresolved split is
    surfaced so the frontend can render the path-mapping hint.
    """
    db, integration_id, resolved_paths, media_ids = env
    await _seed_playback_events(
        db,
        integration_id=integration_id,
        resolved_paths=resolved_paths,
        media_id_by_path=media_ids,
        resolved_count=0,
        unresolved_count=25,
    )

    async with db.session() as session:
        analyzer = PlaybackAnalyzer(session=session)
        outcome = await analyzer.analyze()

    # The total is the count operators see on the card.
    assert outcome.examined_events_total == 25
    # Zero resolved → analyzer correctly skips heuristics
    # (it can't read MediaFile attrs that aren't there).
    assert outcome.examined_events_resolved == 0
    assert outcome.examined_events_unresolved == 25
    # Backwards-compat: ``examined_events`` still equals the
    # resolved-only count (Stage 16 callers depend on this).
    assert outcome.examined_events == 0
    # Below the 20-event analyzer floor for resolved events,
    # so the analyzer skipped. The frontend reads this flag
    # to render the empty-state copy.
    assert outcome.skipped_too_few_events is True


# ── Test 2 — Mixed resolution split ────────────────────────────


@pytest.mark.asyncio
async def test_count_query_reports_split_with_mixed_resolution(env) -> None:
    """Operators with partly-working path mappings see some
    events resolve. The analyzer reports the breakdown so the
    dashboard can render "25 playbacks — 15 resolved, 10
    couldn't be matched"."""
    db, integration_id, resolved_paths, media_ids = env
    await _seed_playback_events(
        db,
        integration_id=integration_id,
        resolved_paths=resolved_paths,
        media_id_by_path=media_ids,
        resolved_count=15,
        unresolved_count=10,
    )

    async with db.session() as session:
        analyzer = PlaybackAnalyzer(session=session)
        outcome = await analyzer.analyze()

    assert outcome.examined_events_total == 25
    assert outcome.examined_events_resolved == 15
    assert outcome.examined_events_unresolved == 10
    # ``examined_events`` backwards-compat = resolved.
    assert outcome.examined_events == 15
    # 15 resolved is still below the 20-event floor.
    assert outcome.skipped_too_few_events is True


# ── Test 3 — Above-floor case clears the skip flag ─────────────


@pytest.mark.asyncio
async def test_count_query_above_floor_runs_heuristics(env) -> None:
    """Once enough RESOLVED events exist (>=20), the analyzer
    iterates over them and ``skipped_too_few_events`` is
    False. The total / resolved / unresolved fields still
    populate so the frontend can show "25 playbacks (25
    resolved, 0 couldn't be matched)" without ambiguity."""
    db, integration_id, resolved_paths, media_ids = env
    await _seed_playback_events(
        db,
        integration_id=integration_id,
        resolved_paths=resolved_paths,
        media_id_by_path=media_ids,
        resolved_count=25,
        unresolved_count=0,
    )

    async with db.session() as session:
        analyzer = PlaybackAnalyzer(session=session)
        outcome = await analyzer.analyze()

    assert outcome.examined_events_total == 25
    assert outcome.examined_events_resolved == 25
    assert outcome.examined_events_unresolved == 0
    assert outcome.skipped_too_few_events is False


# ── Test 4 — Events older than 30 days don't count ─────────────


@pytest.mark.asyncio
async def test_count_query_ignores_events_outside_window(env) -> None:
    """The 30-day cutoff applies to both the total and the
    resolved query. An event 45 days old doesn't bump either
    count."""
    db, integration_id, resolved_paths, media_ids = env
    # Seed 10 fresh + 10 ancient. Distinct ``tag`` keeps the
    # upstream_ids non-colliding.
    await _seed_playback_events(
        db,
        integration_id=integration_id,
        resolved_paths=resolved_paths,
        media_id_by_path=media_ids,
        resolved_count=10,
        unresolved_count=0,
        age_days=1,
        tag="fresh",
    )
    await _seed_playback_events(
        db,
        integration_id=integration_id,
        resolved_paths=resolved_paths,
        media_id_by_path=media_ids,
        resolved_count=10,
        unresolved_count=10,
        age_days=45,
        tag="ancient",
    )

    async with db.session() as session:
        analyzer = PlaybackAnalyzer(session=session)
        outcome = await analyzer.analyze()

    # Only the 10 fresh resolved events count.
    assert outcome.examined_events_total == 10
    assert outcome.examined_events_resolved == 10
    assert outcome.examined_events_unresolved == 0


# ── Test 5 — Empty state ───────────────────────────────────────


@pytest.mark.asyncio
async def test_count_query_with_no_events_returns_zero(env) -> None:
    """Fresh install with no playback data. All three fields
    return 0 so the frontend renders the "let some playback
    accumulate" empty-state copy."""
    db, _, _, _ = env
    async with db.session() as session:
        analyzer = PlaybackAnalyzer(session=session)
        outcome = await analyzer.analyze()

    assert outcome.examined_events_total == 0
    assert outcome.examined_events_resolved == 0
    assert outcome.examined_events_unresolved == 0
    assert outcome.examined_events == 0
    assert outcome.skipped_too_few_events is True
