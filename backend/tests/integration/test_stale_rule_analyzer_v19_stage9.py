"""v1.9 Stage 9.2 — Stale rule analyzer.

Pins:
  1. A rule that's been evaluated recently with zero matches is
     flagged inactive.
  2. A rule that's never been evaluated is NOT flagged (engine
     hasn't seen it yet).
  3. A rule last evaluated outside the analysis window is NOT
     flagged (engine config problem, not a rule problem).
  4. A rule that matched files is NOT flagged inactive.
  5. Without any device signal, the overzealous heuristic is
     skipped entirely.
  6. With device signal AND a high direct_play ratio in the
     window, a firing rule gets the overzealous flag.
  7. Below the minimum sample threshold the overzealous heuristic
     doesn't fire (false positives cost trust).
  8. A non-firing rule isn't double-flagged (inactive wins; the
     overzealous heuristic skips when last_match_count==0).
  9. Suggestion dict shape is stable for the API.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest
import pytest_asyncio

from app.models.playback import PlaybackEvent
from app.models.playback_device import PlaybackDevice
from app.models.rule import Rule
from app.services.playback.stale_rule_analyzer import (
    INACTIVE_WINDOW_DAYS,
    OVERZEALOUS_MIN_SAMPLES,
    StaleRuleAnalyzer,
)
from app.storage.base import Base
from app.storage.database import get_database


@pytest_asyncio.fixture
async def db_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "stage92.db"
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
    yield db
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await db.disconnect()
    get_settings.cache_clear()


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.UTC)


def _seed_rule(
    sess,
    *,
    name: str,
    enabled: bool = True,
    last_evaluated_at: _dt.datetime | None = None,
    last_match_count: int = 0,
) -> Rule:
    rule = Rule(
        name=name,
        enabled=enabled,
        priority=100,
        definition={
            "match": {"field": "video_codec", "op": "eq", "value": "hevc"},
            "actions": [
                {"type": "set_severity", "severity": "warn"},
            ],
        },
        last_evaluated_at=last_evaluated_at,
        last_match_count=last_match_count,
    )
    sess.add(rule)
    return rule


# ── Inactive heuristic ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_inactive_rule_is_flagged(db_session) -> None:
    async with db_session.session() as sess:
        _seed_rule(
            sess,
            name="hevc-fat",
            last_evaluated_at=_now() - _dt.timedelta(hours=1),
            last_match_count=0,
        )
        await sess.commit()
    async with db_session.session() as sess:
        outcome = await StaleRuleAnalyzer(session=sess).analyze()
    flagged = [s for s in outcome.suggestions if s.heuristic == "inactive"]
    assert len(flagged) == 1
    assert flagged[0].rule_name == "hevc-fat"


@pytest.mark.asyncio
async def test_never_evaluated_rule_is_not_flagged(db_session) -> None:
    """A rule the engine hasn't seen yet shouldn't be flagged —
    we have no signal."""
    async with db_session.session() as sess:
        _seed_rule(sess, name="brand-new", last_evaluated_at=None)
        await sess.commit()
    async with db_session.session() as sess:
        outcome = await StaleRuleAnalyzer(session=sess).analyze()
    assert outcome.suggestions == []


@pytest.mark.asyncio
async def test_rule_evaluated_outside_window_is_not_flagged(
    db_session,
) -> None:
    """Last eval >30 days ago — engine might be off; don't
    suggest deletion."""
    async with db_session.session() as sess:
        _seed_rule(
            sess,
            name="stale-engine",
            last_evaluated_at=_now()
            - _dt.timedelta(days=INACTIVE_WINDOW_DAYS + 10),
            last_match_count=0,
        )
        await sess.commit()
    async with db_session.session() as sess:
        outcome = await StaleRuleAnalyzer(session=sess).analyze()
    assert outcome.suggestions == []


@pytest.mark.asyncio
async def test_matching_rule_is_not_flagged_inactive(db_session) -> None:
    async with db_session.session() as sess:
        _seed_rule(
            sess,
            name="actively-matching",
            last_evaluated_at=_now() - _dt.timedelta(hours=1),
            last_match_count=42,
        )
        await sess.commit()
    async with db_session.session() as sess:
        outcome = await StaleRuleAnalyzer(session=sess).analyze()
    inactive = [s for s in outcome.suggestions if s.heuristic == "inactive"]
    assert inactive == []


# ── Overzealous heuristic ──────────────────────────────────────


@pytest.mark.asyncio
async def test_overzealous_skipped_without_device_signal(
    db_session,
) -> None:
    """No PlaybackDevice rows → no device signal → skip the
    overzealous heuristic. The inactive heuristic still runs."""
    async with db_session.session() as sess:
        _seed_rule(
            sess,
            name="firing",
            last_evaluated_at=_now() - _dt.timedelta(hours=1),
            last_match_count=10,
        )
        await sess.commit()
    async with db_session.session() as sess:
        outcome = await StaleRuleAnalyzer(session=sess).analyze()
    overzealous = [
        s for s in outcome.suggestions if s.heuristic == "overzealous"
    ]
    assert overzealous == []


@pytest.mark.asyncio
async def test_overzealous_flagged_when_direct_play_dominates(
    db_session,
) -> None:
    """High direct_play ratio + firing rule + sufficient
    samples → flagged."""
    from app.models.integration import Integration
    from app.models.library import Library

    async with db_session.session() as sess:
        # Library + integration so PlaybackEvent FKs validate
        lib = Library(name="L", root_path="/m", kind="movies")
        sess.add(lib)
        await sess.flush()
        integ = Integration(
            name="i", kind="stub", enabled=True, config={}
        )
        sess.add(integ)
        await sess.flush()
        # Need device signal
        sess.add(
            PlaybackDevice(
                integration_id=integ.id,
                client_key="k1",
                name="LR",
                playback_count=30,
                direct_play_count=25,
                transcode_count=5,
            )
        )
        # Seed events with mostly direct_play decisions
        for i in range(OVERZEALOUS_MIN_SAMPLES + 5):
            decision = "direct_play" if i < OVERZEALOUS_MIN_SAMPLES else "transcode"
            sess.add(
                PlaybackEvent(
                    integration_id=integ.id,
                    upstream_id=f"e{i}",
                    source_path=f"/m/{i}.mkv",
                    decision=decision,
                    started_at=_now() - _dt.timedelta(hours=i),
                )
            )
        # Firing rule
        _seed_rule(
            sess,
            name="firing-overzealous",
            last_evaluated_at=_now() - _dt.timedelta(hours=1),
            last_match_count=10,
        )
        await sess.commit()
    async with db_session.session() as sess:
        outcome = await StaleRuleAnalyzer(session=sess).analyze()
    overzealous = [
        s for s in outcome.suggestions if s.heuristic == "overzealous"
    ]
    assert len(overzealous) == 1
    assert overzealous[0].rule_name == "firing-overzealous"
    ratio = overzealous[0].evidence["direct_play_ratio"]
    assert ratio > 0.5


@pytest.mark.asyncio
async def test_overzealous_skipped_below_sample_minimum(db_session) -> None:
    """Fewer than MIN_SAMPLES events → don't flag, even if all
    were direct_play. We don't want low-volume false positives."""
    from app.models.integration import Integration
    from app.models.library import Library

    async with db_session.session() as sess:
        lib = Library(name="L", root_path="/m", kind="movies")
        sess.add(lib)
        await sess.flush()
        integ = Integration(name="i", kind="stub", enabled=True, config={})
        sess.add(integ)
        await sess.flush()
        sess.add(
            PlaybackDevice(
                integration_id=integ.id,
                client_key="k1",
                playback_count=5,
                direct_play_count=5,
            )
        )
        # Way below threshold
        for i in range(3):
            sess.add(
                PlaybackEvent(
                    integration_id=integ.id,
                    upstream_id=f"e{i}",
                    source_path=f"/m/{i}.mkv",
                    decision="direct_play",
                    started_at=_now() - _dt.timedelta(hours=i),
                )
            )
        _seed_rule(
            sess,
            name="firing",
            last_evaluated_at=_now() - _dt.timedelta(hours=1),
            last_match_count=10,
        )
        await sess.commit()
    async with db_session.session() as sess:
        outcome = await StaleRuleAnalyzer(session=sess).analyze()
    overzealous = [
        s for s in outcome.suggestions if s.heuristic == "overzealous"
    ]
    assert overzealous == []


@pytest.mark.asyncio
async def test_non_firing_rule_not_double_flagged(db_session) -> None:
    """A rule with last_match_count=0 is inactive only — the
    overzealous heuristic explicitly skips it."""
    from app.models.integration import Integration
    from app.models.library import Library

    async with db_session.session() as sess:
        lib = Library(name="L", root_path="/m", kind="movies")
        sess.add(lib)
        await sess.flush()
        integ = Integration(name="i", kind="stub", enabled=True, config={})
        sess.add(integ)
        await sess.flush()
        sess.add(
            PlaybackDevice(
                integration_id=integ.id,
                client_key="k1",
                playback_count=100,
                direct_play_count=100,
            )
        )
        for i in range(OVERZEALOUS_MIN_SAMPLES + 5):
            sess.add(
                PlaybackEvent(
                    integration_id=integ.id,
                    upstream_id=f"e{i}",
                    source_path=f"/m/{i}.mkv",
                    decision="direct_play",
                    started_at=_now() - _dt.timedelta(hours=i),
                )
            )
        _seed_rule(
            sess,
            name="not-firing",
            last_evaluated_at=_now() - _dt.timedelta(hours=1),
            last_match_count=0,
        )
        await sess.commit()
    async with db_session.session() as sess:
        outcome = await StaleRuleAnalyzer(session=sess).analyze()
    # Only inactive flagged.
    flagged = {s.heuristic for s in outcome.suggestions}
    assert "inactive" in flagged
    assert "overzealous" not in flagged


@pytest.mark.asyncio
async def test_suggestion_dict_shape_is_stable(db_session) -> None:
    async with db_session.session() as sess:
        _seed_rule(
            sess,
            name="x",
            last_evaluated_at=_now() - _dt.timedelta(hours=1),
            last_match_count=0,
        )
        await sess.commit()
    async with db_session.session() as sess:
        outcome = await StaleRuleAnalyzer(session=sess).analyze()
    assert len(outcome.suggestions) == 1
    d = outcome.suggestions[0].to_dict()
    assert set(d.keys()) == {
        "rule_id",
        "rule_name",
        "heuristic",
        "reason",
        "evidence",
    }
