"""Analyzer heuristic tests (Stage 16 Turn 2).

We seed the playback_events table with hand-crafted patterns that
each heuristic should pick up, then run the analyzer and inspect the
resulting RuleSuggestion rows.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.integration import Integration
from app.models.library import Library
from app.models.media import MediaFile
from app.models.playback import PlaybackEvent
from app.models.rule_suggestion import RuleSuggestion
from app.services.playback import PlaybackAnalyzer
from app.storage.base import Base
from app.storage.database import get_database


@pytest_asyncio.fixture
async def analyzer_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Spin up an isolated DB with one library, one integration, and
    a seed of 200 media files. The actual playback_events are seeded
    per-test by the helper functions below."""
    db_path = tmp_path / "analyzer.db"
    monkeypatch.setenv("AUDITARR_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("AUDITARR_SECRET_KEY", "test-key-must-be-at-least-sixteen-chars")

    from app.core.settings import get_settings

    get_settings.cache_clear()
    db = get_database()
    db._engine = None  # noqa: SLF001
    db._sessionmaker = None  # noqa: SLF001

    await db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    file_ids: list[str] = []
    integration_id: str
    async with db.session() as session:
        lib = Library(name="Movies", root_path="/mnt/media/Movies", kind="movies")
        session.add(lib)
        await session.flush()

        integration = Integration(
            name="Stub",
            kind="stubplex",
            enabled=True,
            poll_interval_seconds=900,
            config={"base_url": "http://stub/"},
            health_status="ok",
        )
        session.add(integration)
        await session.flush()
        integration_id = integration.id

        for i in range(200):
            mf = MediaFile(
                library_id=lib.id,
                path=f"/mnt/media/Movies/file-{i:04d}.mkv",
                relative_path=f"file-{i:04d}.mkv",
                filename=f"file-{i:04d}.mkv",
                extension="mkv",
                size_bytes=1024 * 1024 * 100,
                mtime=_dt.datetime.now(_dt.UTC),
                category="media",
                severity="ok",
                severity_rank=10,
                has_subtitles=False,
                seen_at=_dt.datetime.now(_dt.UTC),
                is_orphaned=False,
            )
            session.add(mf)
            await session.flush()
            file_ids.append(mf.id)
        await session.commit()

    yield {
        "db": db,
        "integration_id": integration_id,
        "file_ids": file_ids,
    }

    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await db.disconnect()
    get_settings.cache_clear()


def _make_event(
    *,
    integration_id: str,
    file_id: str,
    upstream_id: str,
    decision: str,
    source_codec: str | None = None,
    source_bitrate_kbps: int | None = None,
    source_width: int | None = None,
    source_height: int | None = None,
    source_container: str | None = None,
    reason_code: str | None = None,
    device_kind: str | None = None,
    minutes_ago: int = 60,
) -> PlaybackEvent:
    return PlaybackEvent(
        integration_id=integration_id,
        media_file_id=file_id,
        source_path=f"/mnt/media/Movies/{upstream_id}.mkv",
        decision=decision,
        reason_code=reason_code,
        source_codec=source_codec,
        source_bitrate_kbps=source_bitrate_kbps,
        source_width=source_width,
        source_height=source_height,
        source_container=source_container,
        device_kind=device_kind,
        upstream_id=upstream_id,
        started_at=_dt.datetime.now(_dt.UTC)
        - _dt.timedelta(minutes=minutes_ago),
    )


# ── Threshold guard ──────────────────────────────────────────
@pytest.mark.asyncio
async def test_analyzer_skips_when_too_few_events(analyzer_env) -> None:
    """Below the minimum-events floor, the analyzer should refuse to
    run any heuristics. We seed only 5 events — well under 20."""
    db = analyzer_env["db"]
    iid = analyzer_env["integration_id"]
    fids = analyzer_env["file_ids"]

    async with db.session() as session:
        for i in range(5):
            session.add(
                _make_event(
                    integration_id=iid,
                    file_id=fids[i],
                    upstream_id=f"e{i}",
                    decision="transcode",
                    source_codec="hevc",
                )
            )
        await session.commit()

    async with db.session() as session:
        outcome = await PlaybackAnalyzer(session=session).analyze()

    assert outcome.skipped_too_few_events is True
    assert outcome.suggestions_created == 0


# ── Heuristic 1: high-transcode codec ───────────────────────
@pytest.mark.asyncio
async def test_high_transcode_codec_emits_suggestion(analyzer_env) -> None:
    """Seed 25 HEVC plays, 22 of them transcoded. Should emit a
    high_transcode_codec suggestion."""
    db = analyzer_env["db"]
    iid = analyzer_env["integration_id"]
    fids = analyzer_env["file_ids"]

    async with db.session() as session:
        # 22 HEVC transcodes
        for i in range(22):
            session.add(
                _make_event(
                    integration_id=iid,
                    file_id=fids[i],
                    upstream_id=f"hevc-tc-{i}",
                    decision="transcode",
                    source_codec="hevc",
                    source_bitrate_kbps=12_000,
                    source_width=1920,
                    source_height=1080,
                    device_kind="Roku",
                )
            )
        # 3 HEVC direct plays — pushes total to 25, transcode rate 88%
        for i in range(3):
            session.add(
                _make_event(
                    integration_id=iid,
                    file_id=fids[22 + i],
                    upstream_id=f"hevc-dp-{i}",
                    decision="direct_play",
                    source_codec="hevc",
                )
            )
        await session.commit()

    async with db.session() as session:
        outcome = await PlaybackAnalyzer(session=session).analyze()

    assert outcome.suggestions_created >= 1
    async with db.session() as session:
        suggestions = (
            (await session.execute(select(RuleSuggestion))).scalars().all()
        )
    hevc = [s for s in suggestions if s.heuristic == "high_transcode_codec"]
    assert len(hevc) == 1
    sug = hevc[0]
    assert "hevc" in sug.name.lower() or "HEVC" in sug.name
    assert sug.evidence["codec"] == "hevc"
    assert sug.evidence["total_plays"] == 25
    assert sug.evidence["transcodes"] == 22
    assert sug.files_affected == 22  # 22 distinct file_ids transcoded
    assert sug.definition["match"]["field"] == "video_codec"
    assert sug.definition["match"]["value"] == "hevc"
    # Confidence should be high — 22/25 plays at 88% rate.
    assert sug.confidence >= 0.6


@pytest.mark.asyncio
async def test_high_transcode_codec_ignores_low_rate(analyzer_env) -> None:
    """If only 30% of plays of a codec transcoded, no suggestion."""
    db = analyzer_env["db"]
    iid = analyzer_env["integration_id"]
    fids = analyzer_env["file_ids"]

    async with db.session() as session:
        # 6 transcodes / 14 direct = 30% rate, below 50% threshold
        for i in range(6):
            session.add(
                _make_event(
                    integration_id=iid,
                    file_id=fids[i],
                    upstream_id=f"h264-tc-{i}",
                    decision="transcode",
                    source_codec="h264",
                )
            )
        for i in range(14):
            session.add(
                _make_event(
                    integration_id=iid,
                    file_id=fids[6 + i],
                    upstream_id=f"h264-dp-{i}",
                    decision="direct_play",
                    source_codec="h264",
                )
            )
        await session.commit()

    async with db.session() as session:
        outcome = await PlaybackAnalyzer(session=session).analyze()

    async with db.session() as session:
        suggestions = (
            (await session.execute(select(RuleSuggestion))).scalars().all()
        )
    h264 = [
        s
        for s in suggestions
        if s.heuristic == "high_transcode_codec"
        and s.evidence.get("codec") == "h264"
    ]
    assert h264 == []
    # outcome.examined_events is 20 → passes total threshold, but no
    # heuristic should fire for h264 specifically.
    assert outcome.examined_events == 20


# ── Heuristic 2: bitrate ceiling ────────────────────────────
@pytest.mark.asyncio
async def test_bitrate_ceiling_emits_suggestion(analyzer_env) -> None:
    """Seed 15 transcoded plays all above 20 Mbps. Should suggest a
    bitrate ceiling rule."""
    db = analyzer_env["db"]
    iid = analyzer_env["integration_id"]
    fids = analyzer_env["file_ids"]

    async with db.session() as session:
        for i in range(15):
            session.add(
                _make_event(
                    integration_id=iid,
                    file_id=fids[i],
                    upstream_id=f"hbr-{i}",
                    decision="transcode",
                    source_bitrate_kbps=20_000 + i * 1000,
                    source_codec="hevc",
                )
            )
        # 5 unrelated direct plays to clear the MIN_EVENTS_TOTAL floor.
        for i in range(5):
            session.add(
                _make_event(
                    integration_id=iid,
                    file_id=fids[15 + i],
                    upstream_id=f"hbr-pad-{i}",
                    decision="direct_play",
                    source_bitrate_kbps=3_000,
                )
            )
        await session.commit()

    async with db.session() as session:
        outcome = await PlaybackAnalyzer(session=session).analyze()

    async with db.session() as session:
        rows = (
            (await session.execute(select(RuleSuggestion))).scalars().all()
        )
    bitrate_suggestions = [r for r in rows if r.heuristic == "bitrate_ceiling"]
    assert len(bitrate_suggestions) == 1
    sug = bitrate_suggestions[0]
    assert sug.definition["match"]["field"] == "bitrate_kbps"
    assert sug.definition["match"]["op"] == "gt"
    # Ceiling should be a round Mbps number near the 25th percentile.
    assert sug.definition["match"]["value"] >= 10_000
    assert sug.evidence["transcoded_above_ceiling"] == 15
    _ = outcome


# ── Heuristic 3: container compatibility ────────────────────
@pytest.mark.asyncio
async def test_container_compat_emits_suggestion(analyzer_env) -> None:
    """Seed 12 transcodes all with container.unsupported reason."""
    db = analyzer_env["db"]
    iid = analyzer_env["integration_id"]
    fids = analyzer_env["file_ids"]

    async with db.session() as session:
        for i in range(12):
            session.add(
                _make_event(
                    integration_id=iid,
                    file_id=fids[i],
                    upstream_id=f"mkv-{i}",
                    decision="transcode",
                    reason_code="video.container.unsupported",
                    source_container="mkv",
                    source_codec="h264",
                )
            )
        # 8 unrelated events to push total past threshold
        for i in range(8):
            session.add(
                _make_event(
                    integration_id=iid,
                    file_id=fids[12 + i],
                    upstream_id=f"dp-{i}",
                    decision="direct_play",
                )
            )
        await session.commit()

    async with db.session() as session:
        await PlaybackAnalyzer(session=session).analyze()

    async with db.session() as session:
        rows = (
            (await session.execute(select(RuleSuggestion))).scalars().all()
        )
    cont = [r for r in rows if r.heuristic == "container_compat"]
    assert len(cont) == 1
    assert cont[0].evidence["container"] == "mkv"
    assert cont[0].definition["match"]["field"] == "container"
    assert cont[0].definition["match"]["value"] == "mkv"


# ── Heuristic 4: resolution mismatch ────────────────────────
@pytest.mark.asyncio
async def test_resolution_mismatch_emits_for_4k_transcodes(analyzer_env) -> None:
    db = analyzer_env["db"]
    iid = analyzer_env["integration_id"]
    fids = analyzer_env["file_ids"]

    async with db.session() as session:
        for i in range(14):
            session.add(
                _make_event(
                    integration_id=iid,
                    file_id=fids[i],
                    upstream_id=f"4k-tc-{i}",
                    decision="transcode",
                    source_width=3840,
                    source_height=2160,
                    source_codec="hevc",
                )
            )
        for i in range(6):
            session.add(
                _make_event(
                    integration_id=iid,
                    file_id=fids[14 + i],
                    upstream_id=f"4k-dp-{i}",
                    decision="direct_play",
                    source_width=3840,
                    source_height=2160,
                )
            )
        await session.commit()

    async with db.session() as session:
        await PlaybackAnalyzer(session=session).analyze()

    async with db.session() as session:
        rows = (
            (await session.execute(select(RuleSuggestion))).scalars().all()
        )
    res = [r for r in rows if r.heuristic == "resolution_mismatch"]
    # Could be 1 — for the 4k bucket only (HEVC codec heuristic might
    # also fire since these are all HEVC, but that's a different
    # suggestion).
    fourk = [r for r in res if r.evidence.get("resolution_class") == "4k"]
    assert len(fourk) == 1
    assert fourk[0].definition["match"]["field"] == "width"
    assert fourk[0].definition["match"]["op"] == "gte"
    assert fourk[0].definition["match"]["value"] == 3800


# ── Heuristic 5: failed playback ────────────────────────────
@pytest.mark.asyncio
async def test_failed_playback_emits_suggestion(analyzer_env) -> None:
    db = analyzer_env["db"]
    iid = analyzer_env["integration_id"]
    fids = analyzer_env["file_ids"]

    async with db.session() as session:
        # Need at least 3 failures + enough total events to clear floor
        for i in range(5):
            session.add(
                _make_event(
                    integration_id=iid,
                    file_id=fids[i],
                    upstream_id=f"fail-{i}",
                    decision="failed",
                    reason_code="audio.codec.unsupported",
                )
            )
        for i in range(20):
            session.add(
                _make_event(
                    integration_id=iid,
                    file_id=fids[5 + i],
                    upstream_id=f"ok-{i}",
                    decision="direct_play",
                )
            )
        await session.commit()

    async with db.session() as session:
        await PlaybackAnalyzer(session=session).analyze()

    async with db.session() as session:
        rows = (
            (await session.execute(select(RuleSuggestion))).scalars().all()
        )
    failed = [r for r in rows if r.heuristic == "failed_playback"]
    assert len(failed) == 1
    sug = failed[0]
    assert sug.evidence["failed_events"] == 5
    assert sug.definition["actions"][0]["type"] == "set_severity"
    assert sug.definition["actions"][0]["severity"] == "error"


# ── Dedup / lifecycle ───────────────────────────────────────
@pytest.mark.asyncio
async def test_analyzer_is_idempotent_across_runs(analyzer_env) -> None:
    """Running the analyzer twice on the same data should not produce
    duplicate suggestions — the dedup_key uniqueness should suppress
    the second insert and instead refresh the pending row."""
    db = analyzer_env["db"]
    iid = analyzer_env["integration_id"]
    fids = analyzer_env["file_ids"]

    async with db.session() as session:
        for i in range(20):
            session.add(
                _make_event(
                    integration_id=iid,
                    file_id=fids[i],
                    upstream_id=f"hevc-{i}",
                    decision="transcode",
                    source_codec="hevc",
                )
            )
        await session.commit()

    async with db.session() as session:
        out1 = await PlaybackAnalyzer(session=session).analyze()
    async with db.session() as session:
        out2 = await PlaybackAnalyzer(session=session).analyze()

    assert out1.suggestions_created >= 1
    assert out2.suggestions_created == 0
    assert out2.skipped_deduped >= 1

    async with db.session() as session:
        rows = (
            (await session.execute(select(RuleSuggestion))).scalars().all()
        )
    # One row per heuristic that fired — no duplicates.
    keys = [r.dedup_key for r in rows]
    assert len(keys) == len(set(keys))


@pytest.mark.asyncio
async def test_analyzer_respects_recent_dismissal(analyzer_env) -> None:
    """Dismissing a suggestion should keep it suppressed on the next
    analyzer run (sticky dismissal)."""
    db = analyzer_env["db"]
    iid = analyzer_env["integration_id"]
    fids = analyzer_env["file_ids"]

    async with db.session() as session:
        for i in range(20):
            session.add(
                _make_event(
                    integration_id=iid,
                    file_id=fids[i],
                    upstream_id=f"hevc-{i}",
                    decision="transcode",
                    source_codec="hevc",
                )
            )
        await session.commit()

    # First run creates the suggestion.
    async with db.session() as session:
        await PlaybackAnalyzer(session=session).analyze()

    # Dismiss it directly via the repository.
    async with db.session() as session:
        from app.services.repositories import RuleSuggestionRepository

        repo = RuleSuggestionRepository(session)
        rows = await repo.list_pending()
        sug = next(r for r in rows if r.heuristic == "high_transcode_codec")
        sug.status = "dismissed"
        sug.dismissed_at = _dt.datetime.now(_dt.UTC)
        await session.commit()

    # Second run should skip it.
    async with db.session() as session:
        out = await PlaybackAnalyzer(session=session).analyze()
    assert out.skipped_dismissed >= 1
    assert out.suggestions_created == 0


# ── v1.9 OP-10 — analyzer reads sessions + events with dedup ───


@pytest.mark.asyncio
async def test_analyzer_reads_sessions_as_primary_source(
    analyzer_env,
) -> None:
    """v1.9 OP-10 caveat 6: stopped sessions in the analysis
    window with media_file_id non-null feed the heuristics as
    the primary source (events are the fallback)."""
    from app.models.playback import PlaybackSession

    db = analyzer_env["db"]
    iid = analyzer_env["integration_id"]
    fids = analyzer_env["file_ids"]

    # Seed 20 STOPPED sessions all transcoding hevc — enough to
    # cross both MIN_EVENTS_TOTAL and the per-heuristic floor.
    now = _dt.datetime.now(_dt.UTC)
    async with db.session() as session:
        for i in range(20):
            session.add(
                PlaybackSession(
                    integration_id=iid,
                    session_key=f"sk-{i}",
                    rating_key=f"rk-{i}",
                    media_file_id=fids[i],
                    source_path=f"/mnt/media/Movies/sess-{i}.mkv",
                    state="stopped",
                    decision="transcode",
                    source_codec="hevc",
                    started_at=now - _dt.timedelta(minutes=60),
                    last_event_at=now - _dt.timedelta(minutes=30),
                )
            )
        await session.commit()

    async with db.session() as session:
        outcome = await PlaybackAnalyzer(session=session).analyze()

    # Sessions show up in the examined count (caveat 6 — primary).
    assert outcome.examined_events_resolved >= 20
    # The high-transcode-codec heuristic should have fired.
    assert outcome.candidates_generated >= 1


@pytest.mark.asyncio
async def test_analyzer_dedup_skips_reconciled_events(
    analyzer_env,
) -> None:
    """v1.9 OP-10 caveat 5: an event tagged
    ``reconciled_with_session_id`` is represented by its session
    row — the analyzer's events-fallback read filters it out so
    the play is counted exactly once.

    Seed: 20 sessions (all reconciled-with-history=True) AND a
    matching reconciled event per session. Without the dedup the
    analyzer would see 40 plays; with the dedup it sees 20.
    """
    from app.models.playback import PlaybackSession

    db = analyzer_env["db"]
    iid = analyzer_env["integration_id"]
    fids = analyzer_env["file_ids"]

    now = _dt.datetime.now(_dt.UTC)
    async with db.session() as session:
        for i in range(20):
            sess = PlaybackSession(
                integration_id=iid,
                session_key=f"sk-dedup-{i}",
                rating_key=f"rk-dedup-{i}",
                media_file_id=fids[i],
                source_path=f"/mnt/media/Movies/dedup-{i}.mkv",
                state="stopped",
                decision="transcode",
                source_codec="hevc",
                started_at=now - _dt.timedelta(minutes=60),
                last_event_at=now - _dt.timedelta(minutes=30),
                reconciled_with_history=True,
            )
            session.add(sess)
        await session.flush()
        # Reconciled events — each tagged with its session id.
        sessions_seeded = (
            (await session.execute(select(PlaybackSession)))
            .scalars()
            .all()
        )
        for sess in sessions_seeded:
            session.add(
                _make_event(
                    integration_id=iid,
                    file_id=sess.media_file_id,
                    upstream_id=f"evt-{sess.session_key}",
                    decision="transcode",
                    source_codec="hevc",
                )
            )
        # Tag events with reconciled_with_session_id.
        await session.flush()
        events_seeded = (
            (await session.execute(select(PlaybackEvent)))
            .scalars()
            .all()
        )
        sess_by_key = {s.session_key: s for s in sessions_seeded}
        for ev in events_seeded:
            sk = ev.upstream_id.removeprefix("evt-")
            ev.reconciled_with_session_id = sess_by_key[sk].id
        await session.commit()

    async with db.session() as session:
        outcome = await PlaybackAnalyzer(session=session).analyze()

    # 20 sessions + 0 fallback events (all reconciled) — not 40.
    assert outcome.examined_events_resolved == 20


@pytest.mark.asyncio
async def test_analyzer_falls_back_to_unreconciled_events(
    analyzer_env,
) -> None:
    """v1.9 OP-10 caveat 6 / 5: events WITHOUT a matching session
    (Jellyfin plays, or Plex plays that completed before SSE was
    running) still drive heuristics via the fallback read."""
    db = analyzer_env["db"]
    iid = analyzer_env["integration_id"]
    fids = analyzer_env["file_ids"]

    async with db.session() as session:
        # 20 unreconciled events (reconciled_with_session_id=None).
        for i in range(20):
            session.add(
                _make_event(
                    integration_id=iid,
                    file_id=fids[i],
                    upstream_id=f"unrec-{i}",
                    decision="transcode",
                    source_codec="hevc",
                )
            )
        await session.commit()

    async with db.session() as session:
        outcome = await PlaybackAnalyzer(session=session).analyze()

    assert outcome.examined_events_resolved == 20
    assert outcome.candidates_generated >= 1
