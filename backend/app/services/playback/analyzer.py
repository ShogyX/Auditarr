"""Playback telemetry analyzer (Stage 16 Turn 2).

Reads recent ``playback_events`` and emits :class:`RuleSuggestion`
rows when it sees recurring patterns that an Auditarr rule could
address.

Design notes:

* All heuristics receive the same in-memory snapshot of events for
  the analysis window — one DB read, many heuristics. This keeps
  the analyzer cheap; it can run daily on every Auditarr install
  without showing up on the resource budget.

* Each heuristic returns ``list[SuggestionCandidate]``. Candidates
  carry a stable ``dedup_key`` so the runner can skip patterns
  already deployed or recently dismissed.

* The runner enforces minimum-sample thresholds *before* calling
  heuristics so small datasets don't generate noisy suggestions.
  The default floor is ``MIN_EVENTS_TOTAL = 20`` events in the
  analysis window — below that we don't bother. Per-heuristic
  thresholds layered on top.

* Confidence is computed as a function of (sample size, signal
  strength). The dashboard surfaces this so operators know which
  suggestions are backed by lots of data and which are
  early-warning.
"""

from __future__ import annotations

import datetime as _dt
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.playback import PlaybackEvent, PlaybackSession
from app.models.rule_suggestion import RuleSuggestion
from app.services.repositories.rule_suggestion import (
    RuleSuggestionRepository,
)
from app.utils.datetime import utcnow

log = get_logger("auditarr.playback.analyzer", category="playback")


# v1.9 OP-10 — heuristics work over either ``PlaybackEvent`` (history
# scrape) or ``PlaybackSession`` (SSE-tracked plays). Both expose the
# same attribute surface for the fields the heuristics consult
# (``source_codec``, ``decision``, ``source_bitrate_kbps``,
# ``source_container``, ``source_width``, ``source_height``,
# ``reason_code``, ``media_file_id``). A union alias documents the
# intent rather than reading as Any at every call site.
_PlaybackRow = PlaybackEvent | PlaybackSession


# ── Thresholds ───────────────────────────────────────────────
ANALYSIS_WINDOW_DAYS = 30
MIN_EVENTS_TOTAL = 20  # don't analyze if fewer than this in the window

# Per-heuristic. Tuned to be conservative for v1 — easier to relax
# later than to deal with operator complaints about noise.
MIN_FAMILY_PLAYS = 10  # codec/container/resolution family needs at least this many
MIN_TRANSCODE_RATE = 0.5  # only suggest if >50% of plays of the family transcoded
MIN_BITRATE_CEILING_KBPS = 10_000  # ignore signals below 10 Mbps source bitrate
MIN_FAILED_PLAYS = 3  # failed-playback heuristic needs this many in window


# ── Candidate shape ──────────────────────────────────────────
@dataclass(slots=True)
class SuggestionCandidate:
    """What a heuristic emits — gets translated into a RuleSuggestion
    by the analyzer runner after dedup/sticky-dismiss filtering."""

    heuristic: str
    name: str
    definition: dict[str, Any]  # validates against RuleDefinition
    evidence: dict[str, Any]
    files_affected: int
    est_runtime_s: int | None
    confidence: float
    dedup_key: str


# ── Analyzer ─────────────────────────────────────────────────
@dataclass(slots=True)
class AnalysisOutcome:
    # ``examined_events`` is the count the analyzer actually
    # iterated over: events with a resolved ``media_file_id``
    # (the heuristics read MediaFile attributes, so unresolved
    # events have nothing to match). Kept as the original
    # field name for backwards compatibility with downstream
    # callers (the rule deployment audit log, log messages,
    # etc.).
    examined_events: int = 0
    candidates_generated: int = 0
    suggestions_created: int = 0
    skipped_deduped: int = 0
    skipped_dismissed: int = 0
    skipped_deployed: int = 0
    skipped_too_few_events: bool = False
    # ── Stage 09 (v1.7) — playback-count fix per plan §482 ──
    # The recommendation card's "N playbacks in the last 30
    # days" empty-state copy must show the *true* count, not
    # just the resolved-only count. With broken path mappings,
    # an operator can see "0 playbacks" even when 25 actual
    # playbacks happened — they just don't resolve to library
    # files. We surface the split so the operator sees the
    # path-mapping gap directly in the UI.
    #
    # ``examined_events_total``     = all events in window
    # ``examined_events_resolved``  = events with media_file_id
    # ``examined_events_unresolved`` = total − resolved
    examined_events_total: int = 0
    examined_events_resolved: int = 0
    examined_events_unresolved: int = 0


class PlaybackAnalyzer:
    """Run heuristics over ``playback_events`` and persist suggestions.

    Stateless. Construct once per analyzer run; pass a session.
    """

    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session
        self._suggestions = RuleSuggestionRepository(session)

    async def analyze(self) -> AnalysisOutcome:
        outcome = AnalysisOutcome()
        cutoff = utcnow() - _dt.timedelta(days=ANALYSIS_WINDOW_DAYS)

        # Stage 09 (plan §482) — total event count in the
        # window, regardless of resolution. This is what the
        # dashboard's recommendation card shows in its empty-
        # state copy: operators need to see the true count
        # (not just resolved) so a path-mapping problem doesn't
        # look like "no playbacks happened at all".
        # v1.9 OP-10 — read sessions as the PRIMARY source +
        # events as the fallback. The SSE writer captures every
        # play (including short ones Plex never records as
        # history); the history poller fills in plays that
        # ended before SSE was connected (e.g. after a worker
        # restart).
        #
        # Caveat 6 of the audit: primary is
        #   PlaybackSession WHERE state='stopped'
        #                     AND started_at >= cutoff
        #                     AND media_file_id IS NOT NULL
        # Fallback is
        #   PlaybackEvent   WHERE started_at >= cutoff
        #                     AND media_file_id IS NOT NULL
        #                     AND reconciled_with_session_id IS NULL
        # The reconciled-event guard prevents double-counting a
        # play that appears in BOTH tables (the SSE row is the
        # source of truth; the event row exists for diagnosability
        # per caveat 4).
        #
        # Caveat 10 (concurrency): the two reads happen back-to-
        # back inside the analyzer's session. The SSE writer may
        # be writing concurrently; we tolerate this by treating
        # the sessions snapshot and events snapshot as
        # point-in-time reads. A session that gets reconciled
        # between the two reads still produces correct output
        # because the event's reconciled_with_session_id guard
        # ensures it's filtered from the fallback set.
        total_events_in_window = (
            await self._session.execute(
                select(func.count())
                .select_from(PlaybackEvent)
                .where(PlaybackEvent.started_at >= cutoff)
            )
        ).scalar_one()
        total_sessions_in_window = (
            await self._session.execute(
                select(func.count())
                .select_from(PlaybackSession)
                .where(
                    PlaybackSession.started_at >= cutoff,
                    PlaybackSession.state == "stopped",
                )
            )
        ).scalar_one()
        outcome.examined_events_total = int(total_events_in_window or 0) + int(
            total_sessions_in_window or 0
        )

        session_rows = (
            await self._session.execute(
                select(PlaybackSession)
                .where(
                    PlaybackSession.state == "stopped",
                    PlaybackSession.started_at >= cutoff,
                    PlaybackSession.media_file_id.is_not(None),
                )
            )
        ).scalars().all()
        event_rows = (
            await self._session.execute(
                select(PlaybackEvent)
                .where(PlaybackEvent.started_at >= cutoff)
                .where(PlaybackEvent.media_file_id.is_not(None))
                # Caveat 5 (analyzer dedup): events flagged as
                # reconciled-against-a-session are represented by
                # their session row; skip them in the fallback
                # read to avoid double-counting.
                .where(PlaybackEvent.reconciled_with_session_id.is_(None))
            )
        ).scalars().all()

        # Combine. Heuristics treat both shapes uniformly because
        # PlaybackSession and PlaybackEvent share the field names
        # the heuristics read (source_codec, decision,
        # source_bitrate_kbps, source_container, source_width,
        # source_height, reason_code, media_file_id).
        rows: list[_PlaybackRow] = [*session_rows, *event_rows]

        outcome.examined_events = len(rows)
        outcome.examined_events_resolved = len(rows)
        outcome.examined_events_unresolved = max(
            0, outcome.examined_events_total - outcome.examined_events_resolved
        )
        if len(rows) < MIN_EVENTS_TOTAL:
            outcome.skipped_too_few_events = True
            log.info(
                "playback.analyzer.skipped",
                reason="too_few_events",
                examined=len(rows),
                examined_total=outcome.examined_events_total,
                examined_unresolved=outcome.examined_events_unresolved,
                floor=MIN_EVENTS_TOTAL,
            )
            return outcome

        # Run every heuristic over the snapshot.
        candidates: list[SuggestionCandidate] = []
        for heuristic in (
            _high_transcode_codec,
            _bitrate_ceiling,
            _container_compatibility,
            _resolution_mismatch,
            _failed_playback,
        ):
            try:
                candidates.extend(heuristic(rows))
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "playback.analyzer.heuristic_failed",
                    heuristic=heuristic.__name__,
                    error=str(exc),
                )
        outcome.candidates_generated = len(candidates)

        # Persist with dedup + sticky-dismissal filtering.
        for candidate in candidates:
            if await self._suggestions.has_deployed(candidate.dedup_key):
                outcome.skipped_deployed += 1
                continue
            if await self._suggestions.has_recent_dismissal(candidate.dedup_key):
                outcome.skipped_dismissed += 1
                continue
            existing = await self._suggestions.get_by_dedup_key(
                candidate.dedup_key
            )
            if existing is not None:
                # Already-pending suggestion — refresh its evidence/
                # counters in case the situation has gotten worse.
                existing.evidence = candidate.evidence
                existing.files_affected = candidate.files_affected
                existing.confidence = candidate.confidence
                existing.est_runtime_s = candidate.est_runtime_s
                outcome.skipped_deduped += 1
                continue
            await self._suggestions.add(
                RuleSuggestion(
                    name=candidate.name,
                    definition=candidate.definition,
                    heuristic=candidate.heuristic,
                    evidence=candidate.evidence,
                    files_affected=candidate.files_affected,
                    est_runtime_s=candidate.est_runtime_s,
                    confidence=candidate.confidence,
                    dedup_key=candidate.dedup_key,
                    status="pending",
                )
            )
            outcome.suggestions_created += 1

        await self._session.commit()
        log.info(
            "playback.analyzer.done",
            examined=outcome.examined_events,
            generated=outcome.candidates_generated,
            created=outcome.suggestions_created,
            deduped=outcome.skipped_deduped,
            dismissed=outcome.skipped_dismissed,
            deployed=outcome.skipped_deployed,
        )
        return outcome


# ── Heuristic 1: high-transcode codecs ───────────────────────
def _high_transcode_codec(
    events: Sequence[_PlaybackRow],
) -> list[SuggestionCandidate]:
    """Codec families where the majority of plays transcoded.

    Looks at events grouped by source codec; emits a suggestion when
    a codec has ≥``MIN_FAMILY_PLAYS`` plays and the transcode rate is
    above ``MIN_TRANSCODE_RATE``. The suggested rule flags files of
    that codec at ``warn`` severity and queues an optimization to
    re-encode to a more compatible codec.

    Evidence shape::

        {
          "codec": "hevc",
          "total_plays": 47,
          "transcodes": 39,
          "transcode_rate": 0.83,
          "top_devices": [{"device": "Roku", "count": 22}, ...]
        }
    """
    by_codec: dict[str, list[PlaybackEvent]] = defaultdict(list)
    for ev in events:
        if not ev.source_codec:
            continue
        by_codec[ev.source_codec.lower()].append(ev)

    out: list[SuggestionCandidate] = []
    for codec, plays in by_codec.items():
        if len(plays) < MIN_FAMILY_PLAYS:
            continue
        transcodes = [p for p in plays if p.decision == "transcode"]
        rate = len(transcodes) / len(plays)
        if rate < MIN_TRANSCODE_RATE:
            continue

        # Count unique affected files for the projection cell.
        affected_files = {p.media_file_id for p in transcodes if p.media_file_id}

        # Top devices contributing the transcodes — useful evidence
        # for the operator deciding whether the rule makes sense.
        device_counter = Counter(
            p.device_kind for p in transcodes if p.device_kind
        )
        top_devices = [
            {"device": d, "count": c}
            for d, c in device_counter.most_common(3)
        ]

        # The rule we'd suggest deploying.
        definition = {
            "match": {
                "field": "video_codec",
                "op": "eq",
                "value": codec,
            },
            "actions": [
                {"type": "set_severity", "severity": "warn"},
                {"type": "add_tag", "tag": f"transcode-{codec}"},
            ],
        }

        # Confidence scales with sample size and rate.
        # 10 plays at 50% rate → 0.50; 100 plays at 90% → 0.95.
        confidence = min(0.99, rate * _sample_size_factor(len(plays)))

        out.append(
            SuggestionCandidate(
                heuristic="high_transcode_codec",
                name=f"Flag {codec.upper()} files that transcode frequently",
                definition=definition,
                evidence={
                    "codec": codec,
                    "total_plays": len(plays),
                    "transcodes": len(transcodes),
                    "transcode_rate": round(rate, 3),
                    "top_devices": top_devices,
                },
                files_affected=len(affected_files),
                # Optimization runtime is hard to estimate without
                # device benchmarks; leave it null and let the
                # frontend show "—".
                est_runtime_s=None,
                confidence=round(confidence, 3),
                dedup_key=f"high_transcode_codec:{codec}",
            )
        )
    return out


# ── Heuristic 2: bitrate ceiling ─────────────────────────────
def _bitrate_ceiling(
    events: Sequence[_PlaybackRow],
) -> list[SuggestionCandidate]:
    """Files whose source bitrate is consistently above what client
    devices can direct-play.

    We bucket transcoded events by source bitrate (rounded down to a
    coarse 5-Mbps bucket so the rule generalizes). If a bucket has
    ≥``MIN_FAMILY_PLAYS`` transcoded plays and the source bitrates
    cluster above ``MIN_BITRATE_CEILING_KBPS``, we suggest a rule
    that flags ``bitrate_kbps gt <ceiling>`` files at ``warn`` and
    queues an optimization.
    """
    transcodes = [
        e
        for e in events
        if e.decision == "transcode"
        and e.source_bitrate_kbps
        and e.source_bitrate_kbps > MIN_BITRATE_CEILING_KBPS
    ]
    if len(transcodes) < MIN_FAMILY_PLAYS:
        return []

    bitrates = sorted(e.source_bitrate_kbps or 0 for e in transcodes)
    # The 25th-percentile of transcoded source bitrates is a sensible
    # floor — pick something high enough that we're not over-firing
    # but low enough that the rule still catches the bulk.
    p25 = bitrates[len(bitrates) // 4]
    # Round to the nearest 1 Mbps for clean rule semantics.
    ceiling = (p25 // 1000) * 1000
    if ceiling < MIN_BITRATE_CEILING_KBPS:
        return []

    affected_files = {e.media_file_id for e in transcodes if e.media_file_id}
    confidence = min(
        0.99, 0.8 * _sample_size_factor(len(transcodes))
    )

    definition = {
        "match": {
            "field": "bitrate_kbps",
            "op": "gt",
            "value": ceiling,
        },
        "actions": [
            {"type": "set_severity", "severity": "warn"},
            {"type": "add_tag", "tag": "bitrate-too-high"},
        ],
    }

    return [
        SuggestionCandidate(
            heuristic="bitrate_ceiling",
            name=f"Flag files above {ceiling} kbps that often transcode",
            definition=definition,
            evidence={
                "ceiling_kbps": ceiling,
                "transcoded_above_ceiling": len(transcodes),
                "p25_bitrate_kbps": p25,
                "min_bitrate_kbps": bitrates[0],
                "max_bitrate_kbps": bitrates[-1],
            },
            files_affected=len(affected_files),
            est_runtime_s=None,
            confidence=round(confidence, 3),
            dedup_key=f"bitrate_ceiling:{ceiling}",
        )
    ]


# ── Heuristic 3: container compatibility ─────────────────────
def _container_compatibility(
    events: Sequence[_PlaybackRow],
) -> list[SuggestionCandidate]:
    """Container formats where transcodes consistently fire because
    the container itself is unsupported.

    We look for events with ``reason_code = "video.container.unsupported"``
    grouped by source container. When one container dominates we
    suggest a rule that flags files in that container.
    """
    by_container: dict[str, list[PlaybackEvent]] = defaultdict(list)
    for ev in events:
        if ev.reason_code != "video.container.unsupported":
            continue
        if not ev.source_container:
            continue
        by_container[ev.source_container.lower()].append(ev)

    out: list[SuggestionCandidate] = []
    for container, plays in by_container.items():
        if len(plays) < MIN_FAMILY_PLAYS:
            continue
        affected_files = {p.media_file_id for p in plays if p.media_file_id}
        confidence = min(0.99, 0.85 * _sample_size_factor(len(plays)))
        definition = {
            "match": {
                "field": "container",
                "op": "eq",
                "value": container,
            },
            "actions": [
                {"type": "set_severity", "severity": "warn"},
                {"type": "add_tag", "tag": f"container-{container}-unsupported"},
            ],
        }
        out.append(
            SuggestionCandidate(
                heuristic="container_compat",
                name=(
                    f"Flag .{container} files — container repeatedly "
                    "blocks direct play"
                ),
                definition=definition,
                evidence={
                    "container": container,
                    "transcodes_due_to_container": len(plays),
                    "top_devices": [
                        {"device": d, "count": c}
                        for d, c in Counter(
                            p.device_kind for p in plays if p.device_kind
                        ).most_common(3)
                    ],
                },
                files_affected=len(affected_files),
                est_runtime_s=None,
                confidence=round(confidence, 3),
                dedup_key=f"container_compat:{container}",
            )
        )
    return out


# ── Heuristic 4: resolution mismatch ─────────────────────────
def _resolution_mismatch(
    events: Sequence[_PlaybackRow],
) -> list[SuggestionCandidate]:
    """Resolution classes where transcodes consistently fire.

    We bucket events into resolution classes (4K / 1440p / 1080p /
    720p / SD) by source_width and look for buckets where the
    transcode rate is high. This typically catches the "all my 4K
    HEVC content transcodes on Apple TV 4th gen" pattern.
    """
    def _class_for(width: int | None, height: int | None) -> str | None:
        if width is None or height is None:
            return None
        if width >= 3800:
            return "4k"
        if width >= 2400:
            return "1440p"
        if width >= 1900:
            return "1080p"
        if width >= 1200:
            return "720p"
        return "sd"

    by_class: dict[str, list[PlaybackEvent]] = defaultdict(list)
    for ev in events:
        klass = _class_for(ev.source_width, ev.source_height)
        if klass is None:
            continue
        by_class[klass].append(ev)

    out: list[SuggestionCandidate] = []
    for klass, plays in by_class.items():
        if len(plays) < MIN_FAMILY_PLAYS:
            continue
        transcodes = [p for p in plays if p.decision == "transcode"]
        rate = len(transcodes) / len(plays)
        if rate < MIN_TRANSCODE_RATE:
            continue
        affected_files = {p.media_file_id for p in transcodes if p.media_file_id}
        # Pick a width floor consistent with the class.
        width_floor = {
            "4k": 3800,
            "1440p": 2400,
            "1080p": 1900,
            "720p": 1200,
            "sd": 0,
        }[klass]
        confidence = min(0.99, rate * _sample_size_factor(len(plays)))

        if width_floor == 0:
            # SD as a positive match isn't useful — skip the suggestion.
            continue
        definition = {
            "match": {
                "field": "width",
                "op": "gte",
                "value": width_floor,
            },
            "actions": [
                {"type": "set_severity", "severity": "warn"},
                {"type": "add_tag", "tag": f"hi-res-{klass}"},
            ],
        }
        out.append(
            SuggestionCandidate(
                heuristic="resolution_mismatch",
                name=f"Flag {klass.upper()} content that frequently transcodes",
                definition=definition,
                evidence={
                    "resolution_class": klass,
                    "width_floor": width_floor,
                    "total_plays": len(plays),
                    "transcodes": len(transcodes),
                    "transcode_rate": round(rate, 3),
                },
                files_affected=len(affected_files),
                est_runtime_s=None,
                confidence=round(confidence, 3),
                dedup_key=f"resolution_mismatch:{klass}",
            )
        )
    return out


# ── Heuristic 5: failed playback ─────────────────────────────
def _failed_playback(
    events: Sequence[_PlaybackRow],
) -> list[SuggestionCandidate]:
    """Files that failed playback outright in the analysis window.

    Failures are usually one of: corrupt file, unsupported track
    combination, or DRM-tied container. We surface the affected files
    by filename and suggest a rule that flags them at ``error``
    severity so they show up in the operator's normal severity
    workflow. Deploying the rule makes those specific files
    severity=error; the operator can then investigate them in the
    Files page.

    Note: unlike the other heuristics this isn't a "pattern" rule —
    it's a "specific list of files" rule. If the operator deletes or
    re-encodes a file, the rule still references the old filename
    (harmlessly — it just won't match anything). The dedup key is
    bound to the file set so a new failure cluster surfaces as a
    fresh suggestion.
    """
    failed = [e for e in events if e.decision == "failed"]
    if len(failed) < MIN_FAILED_PLAYS:
        return []
    affected_ids = {e.media_file_id for e in failed if e.media_file_id}
    if not affected_ids:
        return []

    # Build a fast lookup of media_file_id → filename from the events
    # themselves (avoids a DB roundtrip for what's essentially
    # presentation data). We pull the filename from source_path's
    # basename — close enough for a leaf condition match.
    filenames_by_id: dict[str, str] = {}
    for e in failed:
        if e.media_file_id and e.media_file_id not in filenames_by_id:
            base = e.source_path.rsplit("/", 1)[-1]
            if base:
                filenames_by_id[e.media_file_id] = base
    filenames = sorted(filenames_by_id.values())
    if not filenames:
        return []

    # Cap the rule at 50 files so we don't write multi-kilobyte
    # rule definitions that bloat every evaluation. If more files
    # failed, the rule covers the top 50 and the evidence shows the
    # full count.
    rule_filenames = filenames[:50]

    # Match shape: filename `in` <list>. Validated by the rule schema
    # — STRING_OPS includes "in" which behaves as membership for
    # string fields per app.rules.evaluator.
    definition: dict[str, Any] = {
        "match": {
            "field": "filename",
            "op": "in",
            "value": rule_filenames,
        },
        "actions": [
            {"type": "set_severity", "severity": "error"},
            {"type": "add_tag", "tag": "playback-failed"},
        ],
    }
    confidence = min(
        0.95, 0.5 + 0.1 * (len(failed) / MIN_FAILED_PLAYS)
    )

    return [
        SuggestionCandidate(
            heuristic="failed_playback",
            name=f"Flag {len(affected_ids)} files that failed to play",
            definition=definition,
            evidence={
                "failed_events": len(failed),
                "affected_file_count": len(affected_ids),
                "rule_covers_filenames": len(rule_filenames),
                "sample": [
                    {
                        "source_path": e.source_path,
                        "decision": e.decision,
                        "device_name": e.device_name,
                        "device_kind": e.device_kind,
                        "source_codec": e.source_codec,
                        "reason_code": e.reason_code,
                    }
                    for e in failed[:10]
                ],
                "reason_codes": list(
                    Counter(
                        f.reason_code for f in failed if f.reason_code
                    ).most_common(5)
                ),
            },
            files_affected=len(affected_ids),
            est_runtime_s=None,
            confidence=round(confidence, 3),
            # Dedup key tracks the file set. If the set changes
            # materially, a new suggestion surfaces — which is
            # correct: a new failure cluster is news.
            dedup_key=(
                "failed_playback:"
                + ",".join(sorted(affected_ids)[:10])
            ),
        )
    ]


# ── Helpers ──────────────────────────────────────────────────
def _sample_size_factor(n: int) -> float:
    """Map sample size → multiplier in [0.5, 1.0].

    20 events → ~0.7; 100 → ~0.9; 500+ → 1.0. Used to scale
    confidence so a heuristic firing on a tiny dataset doesn't get
    presented with the same weight as one firing on hundreds of
    plays.
    """
    if n <= 0:
        return 0.5
    import math

    # log10(20) ≈ 1.3, log10(100) ≈ 2, log10(500) ≈ 2.7 — normalize.
    return min(1.0, 0.5 + 0.2 * math.log10(max(n, 1)))


# Silence "unused import" linters; Iterable is used by the type
# checker but eslint-equivalent tools may not see it.
_ = Iterable
_ = field
