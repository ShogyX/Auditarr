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
        # Stage 8 (audit follow-up): emit a progress event every
        # PROGRESS_EVERY files so the UI gets a real progress bar
        # rather than just start/complete. 100 is small enough that
        # a 50k-file library still emits ~500 events (cheap on the
        # WS side; tens of bytes each) but large enough that we
        # don't flood. The first emit happens AFTER ``_enumerate``
        # so the UI has both a numerator and a denominator.
        PROGRESS_EVERY = 100
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
                # Stage 9 (audit follow-up): apply the remaining three
                # dispositions BEFORE the upsert so the row lands with
                # the right initial state:
                #   - ``malicious``  → severity=crit + quarantined
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
                    mf.quarantined = True
                    mf.quarantined_at = utcnow()
                    mf.quarantined_reason = (
                        f"Extension rule: {extension} marked malicious"
                    )
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

                # Stage 8 (audit follow-up): periodic progress emit.
                # Every PROGRESS_EVERY files we ship a snapshot so the
                # UI's progress bar moves. We emit on the modulo
                # boundary AND on the final iteration via the
                # post-loop emit below — the latter covers libraries
                # whose total isn't a multiple of PROGRESS_EVERY.
                if seen % PROGRESS_EVERY == 0:
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
                out.append((rel, p, st))
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
