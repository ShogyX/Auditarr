"""Rule service.

Wraps the pure :mod:`app.rules.evaluator` with persistence:
* loads rules from the DB, parses their definition documents,
* materializes :class:`EvaluationInput` from media file rows + tags,
* aggregates per-rule decisions into a final per-file outcome,
* writes the result back to ``media_files`` (severity + aggregated tags)
  and ``rule_evaluations`` (audit log).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.events.bus import EventBus
from app.models.media import MediaFile
from app.models.rule import Rule
from app.models.rule_evaluation import RuleEvaluation
from app.models.tag import MediaTag
from app.rules.evaluator import (
    EvaluationInput,
    EvaluationResult,
    evaluate,
)
from app.rules.schema import SEVERITY_LEVELS, RuleDefinition
from app.services.repositories import (
    MediaRepository,
    RuleEvaluationRepository,
    RuleRepository,
)
from app.services.repositories.media import MediaFilter
from app.utils.datetime import utcnow

log = get_logger("auditarr.rules.service", category="rules")


@dataclass(slots=True)
class FileOutcome:
    """Aggregated result after running every enabled rule against one file."""

    media_file_id: str
    severity: str
    severity_rank: int
    add_tags: list[str]
    matched_rule_ids: list[str]


class RulesService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        event_bus: EventBus | None = None,
        registry=None,
    ) -> None:
        self._session = session
        self._bus = event_bus
        # Registry is optional — when not provided, ``notify`` actions
        # still get recorded in ``rule_evaluations.actions_summary`` but
        # don't dispatch to channels. This keeps unit tests and dry-run
        # paths working without forcing them to build a registry.
        self._registry = registry
        self._rules = RuleRepository(session)
        self._evals = RuleEvaluationRepository(session)
        self._media = MediaRepository(session)

    # ── Loaders ──────────────────────────────────────────────────
    async def load_enabled(self) -> list[tuple[Rule, RuleDefinition]]:
        """Fetch + parse every enabled rule. Rules whose definitions fail
        validation are skipped (with a warning) so a single bad rule can't
        take down evaluation."""
        rules = await self._rules.list_all(enabled_only=True)
        out: list[tuple[Rule, RuleDefinition]] = []
        for rule in rules:
            try:
                out.append((rule, RuleDefinition.model_validate(rule.definition)))
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "rules.skip_invalid",
                    rule_id=rule.id,
                    name=rule.name,
                    error=str(exc),
                )
        return out

    # ── Input materialization ────────────────────────────────────
    async def build_input(self, media_file: MediaFile) -> EvaluationInput:
        """Look up the file's tags and bundle the row into an EvaluationInput."""
        tag_rows = await self._session.execute(
            select(MediaTag.name).where(MediaTag.media_file_id == media_file.id)
        )
        tags = [r[0] for r in tag_rows]
        return EvaluationInput(
            media_file_id=media_file.id,
            path=media_file.path,
            filename=media_file.filename,
            extension=media_file.extension,
            category=media_file.category,
            container=media_file.container,
            video_codec=media_file.video_codec,
            audio_codec=media_file.audio_codec,
            subtitle_codec=media_file.subtitle_codec,
            width=media_file.width,
            height=media_file.height,
            duration_seconds=media_file.duration_seconds,
            bitrate_kbps=media_file.bitrate_kbps,
            framerate=media_file.framerate,
            size_bytes=media_file.size_bytes or 0,
            has_subtitles=bool(media_file.has_subtitles),
            is_orphaned=bool(media_file.is_orphaned),
            subtitle_languages=list(media_file.subtitle_languages or []),
            audio_languages=list(media_file.audio_languages or []),
            tags=tags,
            # Stage 06 (v1.7) — rule engine extensions. ``probe_failed``
            # is a non-nullable bool; ``vt_status`` is the nullable
            # VT result column (NULL means "never looked up", which
            # is distinct from the literal ``not_found``).
            probe_failed=bool(media_file.probe_failed),
            vt_status=media_file.vt_status,
        )

    # ── Per-file evaluation ──────────────────────────────────────
    async def evaluate_file(
        self,
        media_file: MediaFile,
        rules: Sequence[tuple[Rule, RuleDefinition]] | None = None,
        *,
        persist: bool = True,
    ) -> FileOutcome:
        """Evaluate every enabled rule against one file.

        When ``persist`` is True (the default), this also:
        * upserts a RuleEvaluation row per matched (file, rule),
        * deletes evaluation rows for previously-matched rules that no
          longer match,
        * writes the aggregated severity to ``media_files``,
        * adds any rule-generated tags to ``media_tags``.
        """
        if rules is None:
            rules = await self.load_enabled()

        eval_input = await self.build_input(media_file)
        aggregate = EvaluationResult(
            matched=False, severity="ok", severity_rank=SEVERITY_LEVELS["ok"]
        )
        matched_rule_ids: list[str] = []
        now = utcnow()

        for rule, definition in rules:
            result = evaluate(definition, eval_input)
            if not result.matched:
                continue
            matched_rule_ids.append(rule.id)
            result.merge_into(aggregate)

            if persist:
                await self._evals.upsert(
                    RuleEvaluation(
                        media_file_id=media_file.id,
                        rule_id=rule.id,
                        severity=result.severity or "ok",
                        severity_rank=result.severity_rank,
                        actions_summary={
                            "add_tags": result.add_tags,
                            "queue_optimizations": result.queue_optimizations,
                            "notifications": result.notifications,
                        },
                        evaluated_at=now,
                    )
                )
                # Feed the Stage 7 optimization queue. We do this per-rule
                # so each item knows which rule queued it (audit trail) and
                # so the (file, profile) unique constraint can dedupe
                # across multiple rules that happen to queue the same
                # profile.
                #
                # Stage 07 (v1.7): when a profile has a non-empty
                # ``tag_scope``, the file must carry EVERY listed
                # tag to be eligible (plan §398). Files that
                # don't satisfy the scope are not queued; the
                # rule still records its match (severity/tags)
                # but the queue action is a no-op for that file.
                # This is a soft reject — the rules pipeline
                # doesn't crash, and the operator sees the skip
                # in the structured log.
                if result.queue_optimizations:
                    from app.models.optimization_profile import (
                        OptimizationProfile,
                    )
                    from app.optimization.profile_schema import (
                        ProfileDefinition,
                    )
                    from app.services.repositories import OptimizationRepository

                    opt_repo = OptimizationRepository(self._session)
                    # File's current tag set — read once per rule
                    # rather than once per profile.
                    file_tag_set = set(eval_input.tags)
                    for profile_name in result.queue_optimizations:
                        # Stage 07 tag_scope gate. We look up the
                        # profile to read its schema; if the
                        # profile doesn't exist, fall through to
                        # the legacy upsert (the worker will fail
                        # the item cleanly with "profile not
                        # found", which preserves the pre-Stage-07
                        # behaviour).
                        profile_row_q = await self._session.execute(
                            select(OptimizationProfile).where(
                                OptimizationProfile.name == profile_name
                            )
                        )
                        profile_row = profile_row_q.scalar_one_or_none()
                        if profile_row is not None:
                            try:
                                pdef = ProfileDefinition.model_validate(
                                    profile_row.settings
                                )
                            except Exception:  # noqa: BLE001
                                pdef = None
                            if pdef is not None and pdef.tag_scope:
                                missing = [
                                    t for t in pdef.tag_scope
                                    if t not in file_tag_set
                                ]
                                if missing:
                                    log.info(
                                        "rules.queue_skipped_tag_scope",
                                        rule_id=rule.id,
                                        profile=profile_name,
                                        media_file_id=media_file.id,
                                        missing_tags=missing,
                                    )
                                    continue
                        await opt_repo.upsert_queued(
                            media_file_id=media_file.id,
                            profile=profile_name,
                            rule_id=rule.id,
                            queued_at=now,
                        )

                # Fan ``notify`` actions out to notification channels.
                # We dispatch one-per-rule so the audit log can attribute
                # each delivery to the rule that triggered it.
                #
                # Stage 06 (v1.7):
                #   * If the Notify action carries a ``throttle``
                #     config, gate the dispatch through
                #     ``_throttle_gate`` which atomically increments
                #     the per-(rule, window) counter and decides
                #     send-vs-suppress. Suppressed sends emit
                #     ``rule.throttled`` and write ONE summary
                #     audit-log entry per (rule, window) per the
                #     addendum A.2 §125 contract.
                #   * When the SAME rule's actions include both a
                #     ``delete`` and a ``notify``, the dispatcher's
                #     context gets ``auto_delete: True`` so the
                #     email template can render "No action required
                #     — the file is being deleted" (plan §359).
                if result.notifications and self._registry is not None:
                    from app.notifications.dispatcher import NotificationDispatcher

                    dispatcher = NotificationDispatcher(
                        session=self._session,
                        registry=self._registry,
                        event_bus=self._bus,
                    )
                    # Stage 06: rule-level auto_delete signal —
                    # any Delete action on the same rule.
                    auto_delete = any(
                        getattr(a, "type", None) == "delete"
                        for a in definition.actions
                    )
                    for notif in result.notifications:
                        # Stage 06 — apply throttle gate.
                        throttle = notif.get("throttle")
                        if throttle is not None:
                            allow = await self._throttle_gate(
                                rule_id=rule.id,
                                rule_name=rule.name,
                                window_seconds=int(throttle["window_seconds"]),
                                max_per_window=int(throttle["max_per_window"]),
                                now=now,
                            )
                            if not allow:
                                # Suppressed by throttle — skip dispatch.
                                continue
                        try:
                            await dispatcher.dispatch(
                                severity=result.severity or aggregate.severity or "info",
                                rule_id=rule.id,
                                rule_name=rule.name,
                                media_file_id=media_file.id,
                                context={
                                    "path": media_file.path,
                                    "filename": media_file.filename,
                                    # The library_name lookup would be one
                                    # extra query per file; skip for now
                                    # and let templates fall back to "".
                                    "channel": notif.get("channel"),
                                    # Stage 06: signals the email
                                    # template to render the "auto-
                                    # deleting; no action required"
                                    # badge when this rule also
                                    # deletes the file.
                                    "auto_delete": auto_delete,
                                },
                                message_override=notif.get("message"),
                            )
                        except Exception as exc:  # noqa: BLE001
                            log.warning(
                                "rules.notify_dispatch_failed",
                                rule_id=rule.id,
                                error=str(exc),
                            )

        if persist:
            # Drop stale evaluation rows for rules that no longer match.
            stale = await self._session.execute(
                select(RuleEvaluation).where(
                    RuleEvaluation.media_file_id == media_file.id
                )
            )
            for row in stale.scalars().all():
                if row.rule_id not in matched_rule_ids:
                    await self._session.delete(row)

            # Apply severity + tags to the media row.
            media_file.severity = aggregate.severity or "ok"
            media_file.severity_rank = aggregate.severity_rank

            for tag in aggregate.add_tags:
                await self._upsert_tag(media_file.id, tag)

            # Stage 05 (v1.7) — "delete means delete" (Section A.0).
            # The pre-Stage-05 quarantine branch is gone; every
            # ``Delete`` action surfaced in ``aggregate.delete_paths``
            # is applied unconditionally. ``aggregate.delete_reasons``
            # carries the operator-supplied reason (or a synthesized
            # one if none was provided). The audit-log entry picks
            # ``reasons[0]`` because one file produces one delete
            # entry regardless of how many rules matched (the row
            # disappears either way).
            if aggregate.delete_paths:
                reason = (
                    aggregate.delete_reasons[0]
                    if aggregate.delete_reasons
                    else "Deleted by rule"
                )
                await self._hard_delete_media(media_file, reason=reason)

            await self._session.flush()

        return FileOutcome(
            media_file_id=media_file.id,
            severity=aggregate.severity or "ok",
            severity_rank=aggregate.severity_rank,
            add_tags=list(aggregate.add_tags),
            matched_rule_ids=matched_rule_ids,
        )

    async def _throttle_gate(
        self,
        *,
        rule_id: str,
        rule_name: str,
        window_seconds: int,
        max_per_window: int,
        now,
    ) -> bool:
        """Stage 06 (v1.7) — notification throttle gate.

        Per plan §358, throttle survives restart via a DB-backed
        counter (``rule_notification_windows``). One row per
        ``(rule_id, window_start)`` pair.

        Returns ``True`` if the dispatcher should proceed (counter
        was below ``max_per_window`` and has been incremented);
        ``False`` if the dispatch is suppressed.

        On suppression:
          1. Emits a ``rule.throttled`` bus event so the dashboard
             can surface "X notifications suppressed by throttle"
             (addendum A.1 §113).
          2. Writes ONE summary audit-log entry per (rule_id,
             window_start) — addendum A.2 §125 explicitly: "every
             throttle-suppressed notification → one summary entry
             per window per rule (not per suppressed event)". The
             once-per-window guard is the
             ``suppressed_audit_logged`` flag on the row.

        Concurrent calls inside the same evaluation pass are not a
        concern (rules service runs single-threaded per session);
        the once-per-window contract still holds because the row's
        boolean flag is the source of truth and SQLite's row-level
        write lock serialises the UPDATE.
        """
        from datetime import datetime, timedelta

        from app.models.rule_notification_window import (
            RuleNotificationWindow,
        )
        from app.services.audit_service import AuditService

        # Floor ``now`` to the window-seconds boundary so all matches
        # in the same window land on the same row. Using integer
        # seconds since epoch keeps the math obvious and
        # tz-correct (utcnow returns aware datetimes).
        epoch = int(now.timestamp())
        bucket = epoch - (epoch % window_seconds)
        window_start = datetime.fromtimestamp(
            bucket, tz=now.tzinfo
        )
        window_end = window_start + timedelta(seconds=window_seconds)

        # Look up the existing row (if any) for this (rule, window).
        existing_q = await self._session.execute(
            select(RuleNotificationWindow).where(
                RuleNotificationWindow.rule_id == rule_id,
                RuleNotificationWindow.window_start == window_start,
            )
        )
        row = existing_q.scalar_one_or_none()
        if row is None:
            # First match in this window — create the row at
            # count=1 (we're about to deliver) and allow.
            row = RuleNotificationWindow(
                rule_id=rule_id,
                window_start=window_start,
                window_end=window_end,
                count=1,
            )
            self._session.add(row)
            await self._session.flush()
            return True

        if row.count < max_per_window:
            # Under the cap — increment and allow.
            row.count += 1
            await self._session.flush()
            return True

        # ── Throttled ──
        # Suppress this dispatch. Emit the bus event always (the
        # dashboard subscriber can count its own occurrences). The
        # audit log entry, however, is once per (rule, window) per
        # addendum A.2 §125; we use the ``count == max_per_window``
        # transition as the marker — but that's exactly the point
        # we just crossed, and any subsequent suppressed event in
        # the same window will see ``count > max_per_window``.
        if self._bus is not None:
            await self._bus.emit(
                "rule.throttled",
                {
                    "rule_id": rule_id,
                    "rule_name": rule_name,
                    "window_start": window_start.isoformat(),
                    "window_end": window_end.isoformat(),
                    "max_per_window": max_per_window,
                },
                source="rules",
            )

        # Audit-log once per (rule, window). The row's count is the
        # natural signal: exactly when we cross from ``count ==
        # max_per_window`` to ``count == max_per_window + 1`` we
        # write the audit entry; further suppressions in the same
        # window just bump count without re-logging.
        already_logged = row.count > max_per_window
        row.count += 1
        await self._session.flush()
        if not already_logged:
            try:
                audit = AuditService(self._session)
                await audit.record(
                    action="rule.throttled",
                    actor_id=None,
                    actor_label="rules",
                    target_type="rule",
                    target_id=rule_id,
                    metadata={
                        "rule_name": rule_name,
                        "window_start": window_start.isoformat(),
                        "window_end": window_end.isoformat(),
                        "max_per_window": max_per_window,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "rules.throttle_audit_failed",
                    rule_id=rule_id,
                    error=str(exc),
                )
        return False

    async def _hard_delete_media(
        self, media_file: MediaFile, *, reason: str
    ) -> None:
        """Move ``media_file`` to the trash directory and remove the
        ``MediaFile`` row. Filesystem failures are logged but do not
        crash the rules pipeline.

        Stage 05 (v1.7): every successful delete records an audit
        log entry tagged ``file.deleted`` with the operator-supplied
        reason. The entry persists even if the bus event later
        fails to dispatch — the audit trail is the source of truth.
        """
        import shutil
        from pathlib import Path

        from app.core.settings import get_settings
        from app.services.audit_service import AuditService

        settings = get_settings()
        # ``data_dir`` is the configured runtime data path. We carve a
        # ``trash`` subdirectory there so a misconfigured rule is
        # always recoverable — the operator can move the file back.
        trash_root = Path(settings.data_dir) / "trash"
        dst_path: Path | None = None
        try:
            trash_root.mkdir(parents=True, exist_ok=True)
            src = Path(media_file.path)
            if src.exists():
                # Avoid collisions: include the media_file.id in the
                # destination filename so two files of the same name
                # in different libraries don't overwrite each other.
                dst = trash_root / f"{media_file.id}__{src.name}"
                shutil.move(str(src), str(dst))
                dst_path = dst
                log.info(
                    "rules.hard_delete.moved_to_trash",
                    media_file_id=media_file.id,
                    src=str(src),
                    dst=str(dst),
                )
            else:
                log.warning(
                    "rules.hard_delete.source_missing",
                    media_file_id=media_file.id,
                    path=media_file.path,
                )
        except Exception as exc:  # noqa: BLE001
            log.error(
                "rules.hard_delete.failed",
                media_file_id=media_file.id,
                error=str(exc),
            )
            # Don't remove the row if we couldn't trash the file —
            # otherwise the operator loses both the file and the
            # record of it.
            return

        # Stage 05 (v1.7) — audit-log every delete. ``actor_id`` is
        # null here because the rules pipeline runs as the system,
        # not as a specific user; ``actor_label="rules"`` keeps the
        # entry readable. The original path is preserved in metadata
        # so a forensic reader can correlate the trash entry back
        # to the source location even after the row is gone.
        try:
            audit = AuditService(self._session)
            await audit.record(
                action="file.deleted",
                actor_id=None,
                actor_label="rules",
                target_type="media_file",
                target_id=media_file.id,
                metadata={
                    "path": media_file.path,
                    "reason": reason,
                    "trash_path": str(dst_path) if dst_path else None,
                },
            )
        except Exception as exc:  # noqa: BLE001
            # An audit-log write failure shouldn't tank the delete —
            # the file has already been moved to trash. Log loudly
            # so the operator notices the audit gap.
            log.error(
                "rules.hard_delete.audit_failed",
                media_file_id=media_file.id,
                error=str(exc),
            )

        if self._bus is not None:
            await self._bus.emit(
                "media.deleted",
                {
                    "id": media_file.id,
                    "path": media_file.path,
                    "reason": reason,
                },
                source="rules",
            )
        await self._session.delete(media_file)

    # ── Bulk evaluation ──────────────────────────────────────────
    async def evaluate_files(
        self, media_files: Iterable[MediaFile]
    ) -> list[FileOutcome]:
        rules = await self.load_enabled()
        out: list[FileOutcome] = []
        for media_file in media_files:
            out.append(await self.evaluate_file(media_file, rules))
        return out

    async def evaluate_library(
        self,
        library_id: str,
        *,
        tags_any: list[str] | None = None,
    ) -> int:
        """Re-evaluate every file in a library. Returns the file count.

        Stage 18 (audit follow-up): ``tags_any`` scopes the
        re-evaluation to files carrying any of the listed tag names.
        Empty / ``None`` keeps the historical "every file in the
        library" behaviour. The filter is pushed through to
        :class:`MediaFilter` so the database does the work — no
        in-memory tag check per row.
        """
        # Stage 18 (audit follow-up): when ``tags_any`` is requested,
        # we still let the repository walk the library so the
        # filter+pagination story stays simple. The 10k cap below is
        # the same one the historical path uses; libraries that need
        # tag-scoped re-eval over more than 10k tagged rows are rare,
        # and Stage 7's full pagination follow-up will cover both.
        filt = MediaFilter(
            library_id=library_id,
            tags_any=tags_any if tags_any else None,
        )
        page = await self._media.list(filt=filt, offset=0, limit=10_000)
        files = page.items
        await self.evaluate_files(files)

        # Update rule-level "last evaluated" tracking.
        rules = await self._rules.list_all(enabled_only=True)
        now = utcnow()
        for rule in rules:
            count = len(
                await self._evals.list_for_rule(rule.id, limit=10_000)
            )
            rule.last_evaluated_at = now
            rule.last_match_count = count

        return len(files)

    # ── Helpers ──────────────────────────────────────────────────
    async def _upsert_tag(self, media_file_id: str, tag: str) -> None:
        """Add a tag to a media file if not already present.

        Tags added by rules carry ``source='rule'`` so they can be cleaned
        up if a rule is later removed (Stage 7 housekeeping job).
        """
        existing = await self._session.execute(
            select(MediaTag).where(
                MediaTag.media_file_id == media_file_id,
                MediaTag.name == tag,
                MediaTag.source == "rule",
            )
        )
        if existing.scalar_one_or_none() is not None:
            return
        self._session.add(
            MediaTag(media_file_id=media_file_id, name=tag, source="rule")
        )
