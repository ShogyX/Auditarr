"""Scanner service.

Walks a :class:`Library`'s ``root_path``, classifies every file, runs
``ffprobe`` on media candidates, and upserts ``MediaFile`` rows. Files that
were present on the previous scan but are no longer on disk are flagged
``is_orphaned``. The whole flow runs inside a single :class:`ScanRun` and
emits structured events for live progress in the UI.
"""

from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.events.bus import EventBus
from app.models.library import Library
from app.models.media import MediaFile
from app.models.scan_run import ScanRun
from app.services.media.classifier import classify, should_probe
from app.services.media.ffprobe import FfprobeResult, FfprobeService
from app.services.repositories import (
    LibraryRepository,
    MediaExtensionRuleRepository,
    MediaRepository,
    ScanRepository,
)
from app.utils.datetime import utcnow

log = get_logger("auditarr.media.scanner", category="media")


@dataclass(slots=True)
class ScanOptions:
    """Per-invocation knobs."""

    mode: str = "full"  # full | incremental | targeted | rescan
    follow_symlinks: bool = False
    max_files: int | None = None  # safety cap, mostly for tests
    run_rules: bool = True  # evaluate rules after the scan finishes


@dataclass(slots=True)
class ScanReport:
    """Returned to the caller once a scan finishes."""

    run_id: str
    files_seen: int
    files_added: int
    files_updated: int
    files_orphaned: int
    probe_failures: int
    status: str
    error: str | None = None


class Scanner:
    """Coordinator that turns a library directory into MediaFile rows."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        event_bus: EventBus,
        ffprobe: FfprobeService,
        registry=None,
    ) -> None:
        self._session = session
        self._bus = event_bus
        self._ffprobe = ffprobe
        # Registry is optional — passing it through lets the rules
        # evaluation triggered by ``run_rules`` dispatch ``notify``
        # actions. Without it, rule evaluations still happen but
        # notifications stay inert.
        self._registry = registry
        self._libs = LibraryRepository(session)
        self._media = MediaRepository(session)
        self._scans = ScanRepository(session)

    async def scan(
        self, library: Library, *, options: ScanOptions | None = None
    ) -> ScanReport:
        opts = options or ScanOptions()

        run = ScanRun(
            library_id=library.id,
            mode=opts.mode,
            status="running",
            started_at=utcnow(),
            options={"follow_symlinks": opts.follow_symlinks},
        )
        await self._scans.add(run)
        await self._session.commit()  # make the row visible to UI watchers

        await self._bus.emit(
            "scan.started",
            {"run_id": run.id, "library_id": library.id, "mode": opts.mode},
            source="scanner",
        )

        added = updated = orphaned = probe_failures = seen = 0
        # Stage 10 (v1.7) — check VT integration enablement
        # ONCE at scan start. The result gates the per-file
        # ``vt_queue`` insert below so we don't run a SELECT
        # per file. ``vt_enabled`` is True iff at least one
        # integration row with ``kind="virustotal"`` and
        # ``enabled=True`` exists.
        #
        # v1.9 Stage 4.6 — also fetch the integration's options
        # dict so we can apply the scan-scope filter
        # (vt_scan_extensions / categories / required_tags) before
        # enqueuing. Cheap: same query, one extra column.
        from sqlalchemy import select as _select

        from app.models.integration import Integration as _Integration

        _vt_check = await self._session.execute(
            _select(_Integration.id, _Integration.config)
            .where(_Integration.kind == "virustotal")
            .where(_Integration.enabled.is_(True))
            .limit(1)
        )
        _vt_row = _vt_check.first()
        vt_enabled = _vt_row is not None
        # ``config`` is the JSONB blob keyed under
        # ``options`` / ``secrets``. The scope filter lives under
        # options. NoneType-safe so a config from a pre-1.9
        # install (missing the keys entirely) doesn't trip the
        # filter.
        vt_options: dict[str, Any] | None = None
        if _vt_row is not None:
            _vt_config = _vt_row[1] or {}
            if isinstance(_vt_config, dict):
                _opts = _vt_config.get("options")
                if isinstance(_opts, dict):
                    vt_options = _opts
        # Counter so the scan report can surface "N files
        # enqueued for VT lookup" in a future stage. Tracked
        # locally; not persisted on the ScanRun row to avoid
        # a migration in Stage 10.
        vt_enqueued = 0

        # Stage 8 (audit follow-up): emit a progress event every
        # PROGRESS_EVERY files so the UI gets a real progress bar
        # rather than just start/complete. v1.9 Stage 1 tuned this
        # down from 100 to 25 because operators on large libraries
        # reported the bar feeling stuck for tens of seconds at a
        # time; at 25 a 50k-file library emits ~2000 events
        # (still cheap on the WS side; tens of bytes each) and the
        # bar visibly moves between every modulo boundary. The first
        # emit happens AFTER ``_enumerate`` so the UI has both a
        # numerator and a denominator.
        PROGRESS_EVERY = 25
        # v1.9 Stage 1.1 — emit a heartbeat progress event every
        # HEARTBEAT_SECONDS even when ``seen`` hasn't crossed a
        # modulo boundary. This handles the case where a single
        # very-slow ffprobe (mounted-NFS share, oversized file)
        # stalls the loop for longer than the WS keepalive window
        # — without a heartbeat the UI's progress bar appears
        # frozen, indistinguishable from a worker crash. 5 s is
        # short enough to feel alive, long enough not to spam.
        HEARTBEAT_SECONDS = 5.0
        import time as _time

        _last_progress_emit = _time.monotonic()
        # Stage 9 (audit follow-up): load per-extension dispositions
        # once at scan-start. Operators rarely have more than a few
        # dozen rules, so the map is tiny; the dict lookup per file
        # is O(1) and avoids per-file DB queries. ``load_disposition_map``
        # returns enabled rows only — disabled rules are ignored.
        ext_rules_repo = MediaExtensionRuleRepository(self._session)
        disposition_map = await ext_rules_repo.load_disposition_map()
        try:
            paths = self._enumerate(Path(library.root_path), opts)
            # Stage 8: emit the initial progress event with the total
            # estimate. ``files_total_estimate`` is what ``_enumerate``
            # walked — that's an estimate of the upper bound; some
            # files may be filtered (broken symlinks, etc.) before
            # they're counted in ``seen``.
            files_total_estimate = len(paths)
            await self._bus.emit(
                "scan.progress",
                {
                    "run_id": run.id,
                    "library_id": library.id,
                    "files_seen": 0,
                    "files_total_estimate": files_total_estimate,
                },
                source="scanner",
            )
            # Classify everything first; only probe true media candidates.
            for relative, abs_path, st in paths:
                seen += 1
                if opts.max_files is not None and seen > opts.max_files:
                    break

                filename = abs_path.name
                # Stage 9 (audit follow-up): extension-rule lookup
                # short-circuits the rest of the loop body for the
                # ``ignore`` disposition. Lowercase + strip leading
                # dot to match the canonical shape stored on the rule.
                extension = abs_path.suffix.lstrip(".").lower()
                disposition = disposition_map.get(extension)
                if disposition == "ignore":
                    # Skip entirely — file is neither indexed nor
                    # probed nor orphan-tracked for this run. The
                    # operator wanted these out of sight.
                    continue

                cls = classify(filename)
                probe_result: FfprobeResult | None = None
                if cls.category == "media" and should_probe(filename):
                    probe_result = await self._ffprobe.probe(str(abs_path))
                    if not probe_result.ok:
                        probe_failures += 1

                mf = self._build_media_file(
                    library=library,
                    abs_path=abs_path,
                    relative=relative,
                    st=st,
                    category=cls.category,
                    probe=probe_result,
                    last_scan_id=run.id,
                )
                # Stage 9 (audit follow-up), updated Stage 05 (v1.7):
                # apply the remaining three extension-rule dispositions
                # BEFORE the upsert so the row lands with the right
                # initial state:
                #   - ``malicious``  → severity=crit. Stage 05 retired
                #                       the quarantine flag (Section
                #                       A.0 — "delete means delete");
                #                       the row's ``crit`` severity is
                #                       what surfaces the file to the
                #                       operator now. Operators who
                #                       want auto-delete on malicious
                #                       extensions write a rule that
                #                       matches ``severity eq crit``
                #                       AND ``tags contains malicious``
                #                       and applies a Delete action.
                #   - ``accepted``   → severity capped at ok (the
                #                       rule engine still re-runs but
                #                       won't escalate this row)
                #   - ``stats_only`` → indexed (so dashboard counts
                #                       see it) but flagged so the
                #                       rule engine + notifier skip
                #                       further escalation. We use
                #                       ``severity=info`` so the row
                #                       doesn't get a warn/high badge.
                # The rule engine's existing aggregation honours
                # explicit severity values set here.
                if disposition == "malicious":
                    mf.severity = "crit"
                    mf.severity_rank = 5
                elif disposition == "accepted":
                    mf.severity = "ok"
                    mf.severity_rank = 0
                elif disposition == "stats_only":
                    # The rule engine will still see this row, but
                    # the explicit ``info`` makes it a soft hint
                    # rather than a warn/high escalation.
                    mf.severity = "info"
                    mf.severity_rank = 1

                pre_existing = await self._media.get_by_path(str(abs_path))
                saved = await self._media.upsert_by_path(mf)
                if pre_existing is None:
                    added += 1
                    await self._bus.emit(
                        "media.added",
                        {"id": saved.id, "path": saved.path},
                        source="scanner",
                    )
                else:
                    updated += 1

                # Stage 10 (plan §515) — when VT integration is
                # enabled AND the file has a hash AND we haven't
                # already looked it up, enqueue it for VT
                # lookup. Idempotent via the helper's ON CONFLICT
                # DO NOTHING pattern. The actual lookup is done
                # by the (future) drain worker; this layer just
                # makes the work visible to the operator via the
                # status endpoint's ``queue_size`` field.
                #
                # v1.9 Stage 4.6 — apply the scan-scope filter
                # (vt_scan_extensions / categories /
                # required_tags) from the VT integration config.
                # Extension + category are both on ``saved``
                # already (no extra query). ``required_tags`` IS
                # a per-file SELECT against ``media_tags``; we
                # only pay that cost when the operator explicitly
                # configured required tags (default empty list).
                if (
                    vt_enabled
                    and saved.hash_sha256 is not None
                    and saved.vt_status is None
                ):
                    from plugins.virustotal.backend import (
                        enqueue_for_vt_lookup,
                        file_passes_vt_scan_scope,
                    )

                    # Tag fetch only when required.
                    file_tags: list[str] = []
                    if vt_options and vt_options.get("vt_scan_required_tags"):
                        from app.models.tag import MediaTag

                        tag_rows = await self._session.execute(
                            _select(MediaTag.tag).where(
                                MediaTag.media_file_id == saved.id
                            )
                        )
                        file_tags = [r[0] for r in tag_rows.all()]

                    if file_passes_vt_scan_scope(
                        extension=saved.extension,
                        category=saved.category,
                        tags=file_tags,
                        vt_options=vt_options,
                    ):
                        inserted = await enqueue_for_vt_lookup(
                            self._session, media_file_id=saved.id
                        )
                        if inserted:
                            vt_enqueued += 1

                # Stage 8 (audit follow-up): periodic progress emit.
                # Every PROGRESS_EVERY files we ship a snapshot so the
                # UI's progress bar moves. We emit on the modulo
                # boundary AND on the final iteration via the
                # post-loop emit below — the latter covers libraries
                # whose total isn't a multiple of PROGRESS_EVERY.
                # v1.9 Stage 1.1 — also emit a heartbeat every
                # HEARTBEAT_SECONDS even if ``seen`` hasn't crossed a
                # modulo boundary, so a single very-slow probe doesn't
                # make the bar look frozen. ``_now`` is computed once
                # so both branches see the same instant; the heartbeat
                # branch resets ``_last_progress_emit`` so the next
                # tick measures from when this one fired.
                _now = _time.monotonic()
                if (
                    seen % PROGRESS_EVERY == 0
                    or (_now - _last_progress_emit) >= HEARTBEAT_SECONDS
                ):
                    await self._bus.emit(
                        "scan.progress",
                        {
                            "run_id": run.id,
                            "library_id": library.id,
                            "files_seen": seen,
                            "files_total_estimate": files_total_estimate,
                        },
                        source="scanner",
                    )
                    _last_progress_emit = _now

            # Stage 8: final progress event so the bar lands at 100%
            # even when the total isn't a multiple of PROGRESS_EVERY.
            # The ``scan.completed`` event below also implies 100%,
            # but the explicit progress emit gives the UI's progress-
            # bar hook a clean final state to read.
            await self._bus.emit(
                "scan.progress",
                {
                    "run_id": run.id,
                    "library_id": library.id,
                    "files_seen": seen,
                    "files_total_estimate": files_total_estimate,
                },
                source="scanner",
            )

            # Orphans: anything in this library NOT touched by this scan.
            orphaned = await self._media.mark_orphans(
                library.id, last_scan_id=run.id
            )

            run.status = "completed"
            run.finished_at = utcnow()
            run.files_seen = seen
            run.files_added = added
            run.files_updated = updated
            run.files_orphaned = orphaned
            run.probe_failures = probe_failures

            library.last_scan_at = run.finished_at
            library.last_scan_status = "completed"
            library.last_scan_file_count = seen
            await self._session.commit()

            # Re-evaluate rules against everything in this library. Done
            # after the scan commits so a rule-evaluation failure can't
            # corrupt the file index; failures here only affect severity
            # accuracy until the next pass.
            files_evaluated = 0
            if opts.run_rules:
                try:
                    from app.services.rules_service import RulesService

                    rules_service = RulesService(
                        session=self._session,
                        event_bus=self._bus,
                        registry=self._registry,
                    )
                    files_evaluated = await rules_service.evaluate_library(
                        library.id
                    )
                    await self._session.commit()
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "scanner.rules_eval_failed",
                        library_id=library.id,
                        error=str(exc),
                    )
                    await self._session.rollback()

            await self._bus.emit(
                "scan.completed",
                {
                    "run_id": run.id,
                    "library_id": library.id,
                    "files_seen": seen,
                    "files_added": added,
                    "files_updated": updated,
                    "files_orphaned": orphaned,
                    "probe_failures": probe_failures,
                    "files_evaluated": files_evaluated,
                },
                source="scanner",
            )
            return ScanReport(
                run_id=run.id,
                files_seen=seen,
                files_added=added,
                files_updated=updated,
                files_orphaned=orphaned,
                probe_failures=probe_failures,
                status="completed",
            )

        except Exception as exc:  # noqa: BLE001 — surfaced verbatim in the run
            log.exception("scanner.failed", library=library.id)
            run.status = "failed"
            run.finished_at = utcnow()
            run.error = str(exc)[:2000]
            library.last_scan_status = "failed"
            await self._session.commit()
            await self._bus.emit(
                "scan.failed",
                {"run_id": run.id, "library_id": library.id, "error": str(exc)},
                source="scanner",
            )
            return ScanReport(
                run_id=run.id,
                files_seen=seen,
                files_added=added,
                files_updated=updated,
                files_orphaned=orphaned,
                probe_failures=probe_failures,
                status="failed",
                error=str(exc),
            )

    # ── Stage 27: per-file re-probe ──────────────────────────
    async def reprobe_one(self, media_file: MediaFile) -> MediaFile:
        """Re-run ffprobe on a single existing media file in place.

        Use case: the operator notices a file's metadata looks stale
        (a remux replaced the file but the database still shows the
        old codec, or a probe failed mid-scan and now succeeds), and
        asks Auditarr to re-read it without a full library scan.

        Behavior:

        - The file path must still exist on disk. If it doesn't, the
          method marks the row ``is_orphaned=True``, clears the probe
          payload, and returns the updated row. This is the same
          shape the full scan would produce; we don't 404 here
          because the operator just told us they want this file
          checked, and "the file is gone" is itself a useful answer.

        - On a successful probe, the probe payload (container,
          codecs, languages, raw JSON, etc.) is overwritten and
          ``probe_failed`` / ``probe_error`` are cleared. The
          file's path / size_bytes / mtime are NOT updated here
          (that's the scanner's job during a real scan run); the
          operator is asking for a probe refresh, not a metadata
          re-sync. ``seen_at`` IS bumped so the next library scan
          doesn't mistake this for an orphan candidate.

        - On a failed probe, ``probe_failed`` and ``probe_error``
          are set, but existing probe fields are NOT cleared — a
          successful prior probe is better data than no probe.

        - This method does NOT trigger rule re-evaluation. The
          caller is expected to chain ``bulk_reevaluate`` if it
          wants the probe-driven severity recomputed. Keeping the
          two concerns separate matches the existing layering
          (scan → probe; rules service → re-evaluate).
        """
        abs_path = Path(media_file.path)
        # File missing on disk: flag as orphaned, no probe attempt.
        if not abs_path.exists():
            media_file.is_orphaned = True
            media_file.seen_at = utcnow()
            await self._bus.emit(
                "media.reprobed",
                {
                    "id": media_file.id,
                    "ok": False,
                    "orphaned": True,
                },
                source="scanner",
            )
            return media_file

        probe = await self._ffprobe.probe(str(abs_path))
        media_file.seen_at = utcnow()
        # Reset orphan state — we just confirmed it's on disk.
        media_file.is_orphaned = False

        if probe.ok:
            media_file.probe_failed = False
            media_file.probe_error = None
            media_file.container = probe.container
            media_file.duration_seconds = probe.duration_seconds
            media_file.bitrate_kbps = probe.bitrate_kbps
            media_file.video_codec = probe.video_codec
            media_file.audio_codec = probe.audio_codec
            media_file.subtitle_codec = probe.subtitle_codec
            media_file.width = probe.width
            media_file.height = probe.height
            media_file.framerate = probe.framerate
            media_file.has_subtitles = probe.has_subtitles
            media_file.subtitle_languages = probe.subtitle_languages or None
            media_file.audio_languages = probe.audio_languages or None
            media_file.probe = probe.raw
        else:
            media_file.probe_failed = True
            media_file.probe_error = probe.error

        await self._bus.emit(
            "media.reprobed",
            {
                "id": media_file.id,
                "ok": probe.ok,
                "orphaned": False,
            },
            source="scanner",
        )
        return media_file

    # ── helpers ───────────────────────────────────────────────
    def _enumerate(
        self, root: Path, opts: ScanOptions
    ) -> list[tuple[str, Path, os.stat_result]]:
        """Walk *root*, returning ``(relative, absolute, stat)`` for every file."""
        if not root.exists():
            raise FileNotFoundError(f"library root does not exist: {root}")
        if not root.is_dir():
            raise NotADirectoryError(f"library root is not a directory: {root}")

        out: list[tuple[str, Path, os.stat_result]] = []
        # v1.7.2 fix: filenames containing bytes that aren't valid
        # UTF-8 come back from ``os.walk`` with Python's
        # ``surrogateescape`` lone-surrogate substitutions
        # (codepoints U+DC80..U+DCFF). PostgreSQL via asyncpg
        # refuses to bind such strings as VARCHAR — it raises
        # ``UnicodeEncodeError: 'utf-8' codec can't encode
        # character '\udcXX'``. We can't fix the filesystem
        # encoding, so we skip these files and log the skip.
        # The operator can then rename the offending file at the
        # filesystem level. Pinning a count tells them how big
        # the problem is at a glance.
        skipped_bad_encoding = 0
        for dirpath, _dirnames, filenames in os.walk(
            root, followlinks=opts.follow_symlinks
        ):
            for name in filenames:
                p = Path(dirpath) / name
                try:
                    st = p.stat()
                except OSError:
                    continue  # broken symlink, permission denied — skip
                if not _is_regular_file(st.st_mode):
                    continue
                rel = str(p.relative_to(root))
                if _contains_undecodable_bytes(rel) or _contains_undecodable_bytes(str(p)):
                    skipped_bad_encoding += 1
                    if skipped_bad_encoding <= 10:
                        # Cap the log spam at 10 examples; the
                        # final count is still recorded.
                        try:
                            log.warning(
                                "scanner.skipped_bad_encoding",
                                # Use repr() so the log line itself
                                # is encodable (the path contains
                                # surrogates).
                                path_repr=repr(str(p)),
                                detail=(
                                    "Filename contains bytes that aren't "
                                    "valid UTF-8 (surrogateescape "
                                    "codepoints U+DC80..U+DCFF). Rename "
                                    "the file on disk to a valid UTF-8 "
                                    "name so Auditarr can index it."
                                ),
                            )
                        except Exception:  # noqa: BLE001
                            # Defensive: even repr() can fail on some
                            # exotic edge cases; never let logging
                            # crash the scan.
                            pass
                    continue
                out.append((rel, p, st))
        if skipped_bad_encoding:
            log.warning(
                "scanner.skipped_bad_encoding_total",
                count=skipped_bad_encoding,
                detail=(
                    f"Skipped {skipped_bad_encoding} file(s) whose names "
                    "are not valid UTF-8. Run `find <library-root> "
                    "-name '*' -print0 | LC_ALL=C grep -aP '[\\x80-\\xff]'"
                    "` to locate them."
                ),
            )
        return out

    def _build_media_file(
        self,
        *,
        library: Library,
        abs_path: Path,
        relative: str,
        st: os.stat_result,
        category: str,
        probe: FfprobeResult | None,
        last_scan_id: str,
    ) -> MediaFile:
        ext = abs_path.suffix.lstrip(".").lower()
        mtime = _dt.datetime.fromtimestamp(st.st_mtime, tz=_dt.UTC)
        mf = MediaFile(
            library_id=library.id,
            path=str(abs_path),
            relative_path=relative,
            filename=abs_path.name,
            extension=ext,
            size_bytes=st.st_size,
            mtime=mtime,
            inode=int(st.st_ino) if st.st_ino else None,
            category=category,
            severity="ok",
            severity_rank=10,
            has_subtitles=False,
            probe_failed=False,
            last_scan_id=last_scan_id,
            seen_at=utcnow(),
            is_orphaned=False,
        )
        if probe is not None and probe.ok:
            mf.container = probe.container
            mf.duration_seconds = probe.duration_seconds
            mf.bitrate_kbps = probe.bitrate_kbps
            mf.video_codec = probe.video_codec
            mf.audio_codec = probe.audio_codec
            mf.subtitle_codec = probe.subtitle_codec
            mf.width = probe.width
            mf.height = probe.height
            mf.framerate = probe.framerate
            mf.has_subtitles = probe.has_subtitles
            mf.subtitle_languages = probe.subtitle_languages or None
            mf.audio_languages = probe.audio_languages or None
            mf.probe = probe.raw
        elif probe is not None and not probe.ok:
            mf.probe_failed = True
            mf.probe_error = probe.error
        return mf


def _is_regular_file(mode: int) -> bool:
    """``stat.S_ISREG`` without importing ``stat`` everywhere."""
    return (mode & 0o170000) == 0o100000


def _contains_undecodable_bytes(s: str) -> bool:
    """Return True when *s* contains surrogateescape codepoints.

    Python's ``os.walk`` returns filenames as ``str`` even when the
    underlying bytes aren't valid UTF-8. The fallback is the PEP 383
    "surrogateescape" error handler, which substitutes the
    un-decodable byte ``0xNN`` with the Unicode codepoint
    ``0xDC00 + NN`` (in the lone-surrogate range U+DC80..U+DCFF).

    Such strings round-trip through Python fine, but PostgreSQL via
    asyncpg refuses to bind them as VARCHAR — the surrogate isn't
    valid UTF-8 to encode. Detecting these up front lets the
    scanner skip them gracefully instead of crashing the whole
    scan.

    The fast path: try to encode the string as UTF-8; if that
    raises ``UnicodeEncodeError`` we know there's a surrogate (or
    similar) lurking. Cheap for the common case of ASCII / valid
    UTF-8 names (a single C-level encode pass).
    """
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        return True
    return False
