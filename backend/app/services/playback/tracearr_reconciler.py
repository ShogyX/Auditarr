"""v1.9.x — Tracearr → native-library reconciler.

Tracearr's history rows don't expose downstream file paths
(:mod:`backend.plugins.tracearr.backend`); the plugin synthesises a
``tracearr://<serverId>/<mediaType>/<leaf>`` pseudo-path that
satisfies the ``PlaybackEvent.source_path NOT NULL`` contract but
will never join ``media_files.path``. As a result every Tracearr
event lands with ``media_file_id IS NULL`` and the playback analyzer
(which filters ``media_file_id IS NOT NULL``) silently excludes
100% of Tracearr playback from rule-suggestion heuristics — exactly
the symptom the operator sees.

This module is the reconciler. It runs after each ``poll_playback``
tick and, for every Tracearr-kind ``PlaybackEvent`` row with
``media_file_id IS NULL`` in a bounded recent window, parses the
synthesised path back into media identity (title / year / show /
season / episode) and matches it against ``MediaFile.filename``.

Two strategies, tried in order:

  1. **Cross-source reconcile.** If a Plex (or Jellyfin) integration
     observed the same play around the same time, its event row
     already carries a resolved ``media_file_id``. Match by
     ``serverId`` + ±60 s started_at window + normalized title.
     Confidence is high — two independent sources agree.

  2. **Title heuristic against MediaFile.filename.** Generate
     candidate filename patterns following common Plex naming
     conventions (movies as ``"Title (Year).ext"``, episodes as
     ``"Show.SxxExx"``) and run an ILIKE / ratio match against
     ``media_files.filename``. Require a difflib ratio ≥ 0.85 on
     the best candidate to accept; ties → unresolved.

The reconciler is best-effort: a row that can't be matched stays
``media_file_id=NULL`` and a future scan / better metadata may
resolve it later. The next reconciler tick attempts it again.
"""

from __future__ import annotations

import datetime as _dt
import difflib
import re
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.integration import Integration
from app.models.media import MediaFile
from app.models.playback import PlaybackEvent, PlaybackSession
from app.utils.datetime import utcnow

log = get_logger("auditarr.playback.tracearr_reconciler", category="playback")


# How far back the reconciler looks per tick. The history poller
# itself only looks back ``ANALYSIS_WINDOW_DAYS = 30`` for analyzer
# input, so 30 days lines up: every event the analyzer might consider
# gets a chance to be reconciled.
_RECONCILE_WINDOW_DAYS = 30

# Maximum rows to process per tick. Each event drives up to two
# DB queries (cross-source pairing + title heuristic), so the
# total query count is ``2 * batch_limit``. Keep this small enough
# that a full backlog catches up over several ticks rather than
# stalling a single tick past arq's retry timeout. Rows older
# than the cutoff get pruned by housekeeping; rows that aged out
# of the window without being reconciled stay NULL forever
# (correct behaviour: the operator never had the matching
# MediaFile in the first place). v1.9.x — lowered from 5000 to
# 200 after the initial production run timed out poll_playback
# on a host with a deep Tracearr history.
_RECONCILE_BATCH_LIMIT = 200

# Per-tick wall-clock budget. If the reconciler still has rows
# left when this elapses, it commits what it's matched so far
# and returns; the next tick (every 15 minutes, after the next
# poll_playback) continues from where this one left off because
# media_file_id-NULL is the queue cursor.
_RECONCILE_WALL_BUDGET_SECONDS = 25

# Cross-source pairing window — used by the legacy title-fuzzy
# branch when neither side has a resolved media_file_id yet. 60 s
# is a clock-skew floor; the smarter same-server match (see
# ``_shadow_tracearr_against_other_sources``) uses a wider
# duration-aware window instead.
_CROSS_SOURCE_WINDOW = _dt.timedelta(seconds=60)

# v1.9.x — same-server pairing window. Plex's history endpoint
# returns ``viewedAt`` ≈ the time the play crossed the "watched"
# threshold (typically near the end of the play); Tracearr's
# ``startedAt`` is when the play physically began. For a 40-min
# episode the gap is ~37 minutes; for a 2-hour movie it can be
# ~110 minutes. Empirically (from MediaMon, 2026-05-19) the
# observed deltas were 187s, 426s, 2248s, 2261s — clustered just
# below episode/movie duration.
#
# Strategy: for each Plex event we look backwards by
# ``max(duration_s, 7200) + 300s`` (i.e. duration plus a 5-minute
# buffer for short scrub-forward sessions) and forwards by 60s
# (clock skew tolerance). When both events resolved to the same
# media_file_id, we treat them as the same play and shadow the
# Tracearr copy.
_SAME_PLAY_BACKWARD_BUFFER = _dt.timedelta(seconds=300)
_SAME_PLAY_FORWARD_TOLERANCE = _dt.timedelta(seconds=60)
_SAME_PLAY_DURATION_FALLBACK = _dt.timedelta(seconds=7200)  # 2h

# Fuzzy-match threshold for the title heuristic. 0.85 is high enough
# that ``"The Office (2005)"`` doesn't match a random ``"The Office
# UK"`` file but low enough that ``"Star Wars: Episode IV - A New
# Hope (1977)"`` matches the operator's ``"Star Wars Episode 4 - A
# New Hope (1977).mkv"`` file.
_TITLE_MATCH_THRESHOLD = 0.85


@dataclass(slots=True)
class ReconcileOutcome:
    examined: int = 0
    matched_cross_source: int = 0
    matched_title_heuristic: int = 0
    unmatched: int = 0
    error: str | None = None


@dataclass(slots=True)
class ParsedIdentity:
    """What we managed to parse out of a Tracearr pseudo-path."""

    media_type: str  # "movie" | "episode" | "track" | "unknown"
    server_id: str | None
    title: str | None
    year: int | None
    show: str | None
    season: int | None
    episode: int | None


# ── Path parser ─────────────────────────────────────────────────
# Mirrors ``_synth_source_path`` in plugins/tracearr/backend.py. Any
# change to that synthesiser MUST also update this parser.
#
# Shapes (from the synthesiser):
#   tracearr://<serverId>/movie/<Title> (<Year>)
#   tracearr://<serverId>/movie/<Title>                  (year omitted)
#   tracearr://<serverId>/episode/<Show>/S<NN>E<NN> — <Title>
#   tracearr://<serverId>/episode/<Show> — <Title>      (S/E omitted)
#   tracearr://<serverId>/track/<artist>/<album>/<title>
#   tracearr://<serverId>/<other>/<leaf...>

_TRACEARR_PREFIX = "tracearr://"
_EPISODE_LEAF_RE = re.compile(
    r"^(?P<show>.+?)/S(?P<season>\d{1,3})E(?P<episode>\d{1,4})\s+—\s+(?P<title>.+)$"
)
_EPISODE_LEAF_NO_SE_RE = re.compile(
    r"^(?P<show>.+?)\s+—\s+(?P<title>.+)$"
)
_MOVIE_LEAF_WITH_YEAR_RE = re.compile(
    r"^(?P<title>.+?)\s+\((?P<year>\d{4})\)$"
)


def parse_tracearr_pseudo_path(path: str) -> ParsedIdentity | None:
    """Reverse-engineer the identity ``_synth_source_path`` baked
    into the URI. Returns ``None`` when the path isn't a
    tracearr:// URI or its shape is unrecognised.
    """
    if not path or not path.startswith(_TRACEARR_PREFIX):
        return None
    remainder = path[len(_TRACEARR_PREFIX):]
    # remainder = "<serverId>/<mediaType>/<leaf>"
    parts = remainder.split("/", 2)
    if len(parts) < 3:
        return None
    server_id, media_type, leaf = parts[0], parts[1], parts[2]
    leaf = leaf.strip()
    if not leaf:
        return None

    if media_type == "movie":
        match = _MOVIE_LEAF_WITH_YEAR_RE.match(leaf)
        if match:
            return ParsedIdentity(
                media_type="movie",
                server_id=server_id or None,
                title=match.group("title").strip() or None,
                year=int(match.group("year")),
                show=None,
                season=None,
                episode=None,
            )
        return ParsedIdentity(
            media_type="movie",
            server_id=server_id or None,
            title=leaf or None,
            year=None,
            show=None,
            season=None,
            episode=None,
        )

    if media_type == "episode":
        match = _EPISODE_LEAF_RE.match(leaf)
        if match:
            return ParsedIdentity(
                media_type="episode",
                server_id=server_id or None,
                title=match.group("title").strip() or None,
                year=None,
                show=match.group("show").strip() or None,
                season=int(match.group("season")),
                episode=int(match.group("episode")),
            )
        # Fallback: episode rows without S/E numbers (rare; the
        # synthesiser only emits those when the upstream omits
        # them — still try to match by show/title).
        match = _EPISODE_LEAF_NO_SE_RE.match(leaf)
        if match:
            return ParsedIdentity(
                media_type="episode",
                server_id=server_id or None,
                title=match.group("title").strip() or None,
                year=None,
                show=match.group("show").strip() or None,
                season=None,
                episode=None,
            )
        return ParsedIdentity(
            media_type="episode",
            server_id=server_id or None,
            title=leaf or None,
            year=None,
            show=None,
            season=None,
            episode=None,
        )

    # Music tracks are intentionally not matched. Most music
    # libraries don't have ffprobe-rich MediaFile rows worth
    # joining, and the rule heuristics don't operate on music
    # paths today. Return identity so the caller can still log
    # what was skipped.
    return ParsedIdentity(
        media_type=media_type or "unknown",
        server_id=server_id or None,
        title=leaf or None,
        year=None,
        show=None,
        season=None,
        episode=None,
    )


# ── Matching helpers ───────────────────────────────────────────
_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_SE_PATTERN_RE = re.compile(r"s(\d{1,3})e(\d{1,4})", re.IGNORECASE)


def _normalize(text: str) -> str:
    """Lowercase + strip non-alphanumeric so comparisons collapse
    punctuation, articles, separators. ``"Star Wars: Episode IV - A
    New Hope (1977)"`` and ``"Star Wars Episode IV A New Hope
    1977"`` collapse to the same string."""
    return _NORMALIZE_RE.sub("", text.lower())


def _build_ilike_patterns(identity: ParsedIdentity) -> list[str]:
    """Build a list of SQL ILIKE patterns we can pre-filter
    ``media_files.filename`` with before paying for the
    difflib ratio scoring. The DB-side filter trims the
    candidate set from millions to a handful.
    """
    out: list[str] = []
    if identity.media_type == "movie" and identity.title:
        title = identity.title.strip()
        # Match the bare title anywhere in the filename. Plex's
        # naming convention places it at the start, but the
        # operator might have an alternate layout (e.g. quality
        # suffix folders).
        out.append(f"%{title}%")
        if identity.year:
            # The "Title (Year)" form is the high-signal one.
            out.append(f"%{title}%{identity.year}%")
    elif identity.media_type == "episode":
        # Episode SxxExx is the gold-standard discriminator;
        # use it when we have it.
        if identity.season is not None and identity.episode is not None:
            se = f"S{identity.season:02d}E{identity.episode:02d}"
            if identity.show:
                out.append(f"%{identity.show}%{se}%")
            out.append(f"%{se}%")
        elif identity.show and identity.title:
            out.append(f"%{identity.show}%{identity.title}%")
    return out


def _score_filename(
    identity: ParsedIdentity, filename: str
) -> float:
    """Return a similarity ratio in [0, 1] for the candidate
    filename vs the parsed identity. Higher is better.
    """
    norm_file = _normalize(filename)

    if identity.media_type == "episode":
        # When we have SxxExx, require it to appear in the
        # filename exactly. Otherwise the score is bounded low so
        # a fuzzy title match alone can't trigger acceptance.
        if identity.season is not None and identity.episode is not None:
            target_se = f"s{identity.season:02d}e{identity.episode:02d}"
            if target_se not in norm_file:
                return 0.0
            # Strip the SxxExx token from the candidate before the
            # ratio compare so it doesn't dilute the title's
            # contribution. Otherwise "Breaking.Bad.S05E16.Felina"
            # scores below threshold against
            # "breakingbadfelina" purely because the extra
            # ``s05e16`` characters bloat the candidate length.
            # The gate above already proved SxxExx matches; the
            # ratio is purely a disambiguator.
            norm_file = norm_file.replace(target_se, "")
        components: list[str] = []
        if identity.show:
            components.append(identity.show)
        if identity.title:
            components.append(identity.title)
        target = _normalize(" ".join(components)) if components else ""
    else:
        # Movie or unknown. Use title + year.
        components = []
        if identity.title:
            components.append(identity.title)
        if identity.year:
            components.append(str(identity.year))
        target = _normalize(" ".join(components)) if components else ""

    if not target:
        return 0.0

    # v1.9.x — substring containment beats ratio. Plex's canonical
    # naming layout includes the title (and year / SxxExx) as a
    # contiguous prefix, followed by quality / release / encoding
    # metadata. A naive ``difflib.ratio()`` over the whole filename
    # punishes those suffixes: ``"projecthailmary2026"`` vs
    # ``"projecthailmary2026webdl2160pproperkv"`` scores 0.68 even
    # though it's a clear match. Substring containment captures the
    # >95% of real Plex-named files. The ratio remains as a fuzzy
    # safety net for files with reordered tokens.
    if target in norm_file:
        coverage = len(target) / max(len(norm_file), 1)
        return min(1.0, 0.90 + 0.10 * coverage)
    return difflib.SequenceMatcher(None, target, norm_file).ratio()


# ── Cross-source dedup (Pass A) ──────────────────────────────
async def _shadow_tracearr_against_other_sources(
    session: AsyncSession, *, cutoff: _dt.datetime
) -> int:
    """v1.9.x — Iterate Plex (and Jellyfin) playback records that
    have a resolved ``media_file_id`` and find Tracearr events at
    ±60s that observed the same play. Set the Tracearr row's
    ``media_file_id`` (if not already set) and
    ``reconciled_with_session_id`` (to the matched row's id) so
    the analyzer's existing ``IS NULL`` filter skips the Tracearr
    duplicate.

    Returns the count of Tracearr events shadowed in this pass.

    Why iterate Plex (not Tracearr)? Plex history is a bounded
    small set (typically <100 rows in production); Tracearr can
    have thousands. The reverse direction would re-process the
    same Tracearr rows every tick because we don't currently
    have a "this row was dedup-checked" tracking column.

    Caveats:
      * Only matches Tracearr rows whose
        ``reconciled_with_session_id`` is still NULL — re-shadowing
        an already-shadowed row would be a no-op anyway.
      * Score threshold is the same ``_TITLE_MATCH_THRESHOLD`` used
        elsewhere — substring containment + small ratio fallback.
        Plex's filename + Tracearr's synthesised pseudo-path both
        carry the title; the scorer parses the pseudo-path
        identity and matches it against the Plex filename.
    """
    # Bounded set: events from non-tracearr integrations with a
    # resolved media_file_id in the analysis window.
    plex_rows = (
        await session.execute(
            select(PlaybackEvent, MediaFile.filename)
            .join(MediaFile, PlaybackEvent.media_file_id == MediaFile.id)
            .join(Integration, PlaybackEvent.integration_id == Integration.id)
            .where(
                Integration.kind != "tracearr",
                PlaybackEvent.media_file_id.is_not(None),
                PlaybackEvent.started_at >= cutoff,
                MediaFile.category == "media",
            )
        )
    ).all()
    session_rows = (
        await session.execute(
            select(PlaybackSession, MediaFile.filename)
            .join(MediaFile, PlaybackSession.media_file_id == MediaFile.id)
            .join(Integration, PlaybackSession.integration_id == Integration.id)
            .where(
                Integration.kind != "tracearr",
                PlaybackSession.media_file_id.is_not(None),
                PlaybackSession.started_at >= cutoff,
                PlaybackSession.state == "stopped",
                MediaFile.category == "media",
            )
        )
    ).all()

    shadowed = 0
    # Single loop over both source kinds — they expose the
    # ``.id``, ``.started_at``, ``.media_file_id`` surface the
    # match needs.
    candidates: list[tuple[Any, str]] = [
        *((row[0], row[1]) for row in plex_rows),
        *((row[0], row[1]) for row in session_rows),
    ]
    for source_row, filename in candidates:
        # v1.9.x — same-play window. Plex credits viewedAt near
        # the end of a play; Tracearr's startedAt is the start.
        # Look backwards by duration + buffer, forwards by clock-
        # skew tolerance.
        duration = (
            _dt.timedelta(seconds=getattr(source_row, "duration_s", None) or 0)
            if getattr(source_row, "duration_s", None)
            else _SAME_PLAY_DURATION_FALLBACK
        )
        # Some events come through with absurd or missing
        # durations; never look back further than the fallback +
        # buffer so we don't accidentally pair plays of the same
        # file from yesterday.
        backward = min(
            max(duration, _dt.timedelta(seconds=60)),
            _SAME_PLAY_DURATION_FALLBACK,
        ) + _SAME_PLAY_BACKWARD_BUFFER
        window_lo = source_row.started_at - backward
        window_hi = source_row.started_at + _SAME_PLAY_FORWARD_TOLERANCE

        # Primary match: SAME media_file_id (already resolved by
        # both sides). This is the strongest possible signal that
        # both rows describe the same play on the same server.
        tracearr_rows = (
            await session.execute(
                select(PlaybackEvent)
                .join(
                    Integration,
                    PlaybackEvent.integration_id == Integration.id,
                )
                .where(
                    Integration.kind == "tracearr",
                    PlaybackEvent.reconciled_with_session_id.is_(None),
                    PlaybackEvent.media_file_id == source_row.media_file_id,
                    PlaybackEvent.started_at >= window_lo,
                    PlaybackEvent.started_at <= window_hi,
                )
                .order_by(PlaybackEvent.started_at.desc())
            )
        ).scalars().all()

        for tr in tracearr_rows:
            tr.reconciled_with_session_id = source_row.id
            shadowed += 1

        # Secondary match: title-fuzzy on still-unresolved
        # Tracearr rows. Captures plays where Tracearr's title
        # heuristic hasn't yet bound to the same MediaFile (the
        # next title-heuristic pass should catch them, but if we
        # paired by title here it costs one extra fuzzy compare
        # per Plex row and avoids a tick of lag).
        tracearr_unresolved = (
            await session.execute(
                select(PlaybackEvent)
                .join(
                    Integration,
                    PlaybackEvent.integration_id == Integration.id,
                )
                .where(
                    Integration.kind == "tracearr",
                    PlaybackEvent.reconciled_with_session_id.is_(None),
                    PlaybackEvent.media_file_id.is_(None),
                    PlaybackEvent.started_at >= window_lo,
                    PlaybackEvent.started_at <= window_hi,
                )
            )
        ).scalars().all()
        for tr in tracearr_unresolved:
            identity = parse_tracearr_pseudo_path(tr.source_path)
            if identity is None:
                continue
            score = _score_filename(identity, filename)
            if score >= _TITLE_MATCH_THRESHOLD:
                tr.media_file_id = source_row.media_file_id
                tr.reconciled_with_session_id = source_row.id
                shadowed += 1

    return shadowed


# ── Title heuristic ───────────────────────────────────────────
async def _try_title_heuristic(
    session: AsyncSession,
    *,
    identity: ParsedIdentity,
) -> str | None:
    """Match the identity against ``MediaFile.filename`` via a
    DB-side ILIKE pre-filter + Python-side difflib ranking.

    Returns the best-matching media_file_id when its ratio meets
    ``_TITLE_MATCH_THRESHOLD``, else None.
    """
    patterns = _build_ilike_patterns(identity)
    if not patterns:
        return None

    # OR all patterns together for one round trip. Cap the result
    # set so a too-permissive pattern doesn't pull millions of
    # candidate rows into Python.
    conditions = [MediaFile.filename.ilike(p) for p in patterns]
    result = await session.execute(
        select(MediaFile.id, MediaFile.filename)
        .where(
            or_(*conditions),
            # v1.9.x — only match against MEDIA-category files.
            # Tracearr playback reports the operator watched the
            # MEDIA file, not its sidecar; matching ``Movie
            # (2020).en.srt`` or ``Movie (2020).nfo`` would
            # bind playback rules to the wrong file.
            MediaFile.category == "media",
        )
        .limit(200)
    )
    candidates = result.all()
    if not candidates:
        return None

    best_id: str | None = None
    best_score = 0.0
    for mf_id, filename in candidates:
        score = _score_filename(identity, filename)
        if score > best_score:
            best_score = score
            best_id = mf_id
    if best_id is not None and best_score >= _TITLE_MATCH_THRESHOLD:
        return best_id
    return None


# ── Entry point ───────────────────────────────────────────────
async def reconcile_tracearr_playback(
    session: AsyncSession,
) -> ReconcileOutcome:
    """Resolve recent Tracearr PlaybackEvent rows to MediaFile rows.

    Idempotent: rows that already have a media_file_id are skipped.
    Best-effort: an error mid-batch is logged + recorded in the
    outcome; the rest of the batch continues. Called from the worker
    immediately after ``poll_playback``.
    """
    outcome = ReconcileOutcome()
    cutoff = utcnow() - _dt.timedelta(days=_RECONCILE_WINDOW_DAYS)

    # v1.9.x — Pass A: cross-source dedup against Plex history.
    # Iterate the bounded set of Plex/Jellyfin events that have
    # a resolved media_file_id; for each, find Tracearr events
    # at ±60s with a matching title and shadow them. This
    # handles the same-server case (memory:
    # project-mediamon-auditarr) where Plex and Tracearr both
    # observe the same play — without shadowing, the analyzer
    # double-counts transcodes.
    #
    # Walking Plex events (not Tracearr) is the cheap direction:
    # Plex history typically has <100 events while Tracearr can
    # have thousands. The reverse iteration (which I tried first)
    # re-queries the same Tracearr rows every tick because the
    # work-tracking column to "this row was already dedup-checked"
    # doesn't exist on the current schema.
    cross_source_outcome = await _shadow_tracearr_against_other_sources(
        session, cutoff=cutoff
    )
    outcome.matched_cross_source = cross_source_outcome

    # Pass B: title heuristic for any remaining unresolved
    # Tracearr events. Bounded by media_file_id IS NULL so already-
    # resolved (whether by Pass A or a previous title pass) are
    # skipped.
    rows = (
        await session.execute(
            select(PlaybackEvent)
            .join(Integration, PlaybackEvent.integration_id == Integration.id)
            .where(
                Integration.kind == "tracearr",
                PlaybackEvent.media_file_id.is_(None),
                PlaybackEvent.started_at >= cutoff,
            )
            .order_by(PlaybackEvent.started_at.desc())
            .limit(_RECONCILE_BATCH_LIMIT)
        )
    ).scalars().all()

    outcome.examined = len(rows)
    if not rows:
        return outcome

    # v1.9.x — wall-clock budget. The first deploy on MediaMon
    # timed out poll_playback at 62s because a deep Tracearr
    # backlog drove ~2k events worth of cross-source + title
    # queries. Stop mid-batch when this budget is hit; the next
    # tick continues automatically because media_file_id IS NULL
    # is the queue's natural cursor.
    import time as _time
    started_monotonic = _time.monotonic()

    for event in rows:
        if _time.monotonic() - started_monotonic > _RECONCILE_WALL_BUDGET_SECONDS:
            log.info(
                "tracearr_reconciler.budget_exhausted",
                seconds=_RECONCILE_WALL_BUDGET_SECONDS,
                examined_in_tick=(
                    outcome.matched_cross_source
                    + outcome.matched_title_heuristic
                    + outcome.unmatched
                ),
                remaining=len(rows)
                - (
                    outcome.matched_cross_source
                    + outcome.matched_title_heuristic
                    + outcome.unmatched
                ),
            )
            break
        try:
            identity = parse_tracearr_pseudo_path(event.source_path)
            if identity is None:
                # Not a tracearr:// path (perhaps the operator
                # ran a path-mapping that rewrote the URI; or a
                # synthesiser bug). Nothing to reconcile.
                outcome.unmatched += 1
                continue
            if identity.media_type not in ("movie", "episode"):
                # Tracks intentionally skipped (see module
                # docstring).
                outcome.unmatched += 1
                continue

            # Pass A (cross-source) already ran before this loop.
            # Any row reaching this point either had no Plex
            # counterpart or its title didn't match the
            # counterpart's filename. Fall back to library-side
            # title heuristic.
            matched_id = await _try_title_heuristic(
                session, identity=identity
            )
            if matched_id is not None:
                event.media_file_id = matched_id
                outcome.matched_title_heuristic += 1
                continue

            outcome.unmatched += 1
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "tracearr_reconciler.row_failed",
                event_id=event.id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            outcome.unmatched += 1

    await session.commit()
    log.info(
        "tracearr_reconciler.done",
        examined=outcome.examined,
        matched_cross_source=outcome.matched_cross_source,
        matched_title_heuristic=outcome.matched_title_heuristic,
        unmatched=outcome.unmatched,
    )
    return outcome


# Silence unused import flake; kept for symmetry with sibling
# services that use these symbols.
_ = and_
_ = func
_ = Iterable
