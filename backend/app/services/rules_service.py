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
                if result.queue_optimizations:
                    from app.services.repositories import OptimizationRepository

                    opt_repo = OptimizationRepository(self._session)
                    for profile in result.queue_optimizations:
                        await opt_repo.upsert_queued(
                            media_file_id=media_file.id,
                            profile=profile,
                            rule_id=rule.id,
                            queued_at=now,
                        )

                # Fan ``notify`` actions out to notification channels.
                # We dispatch one-per-rule so the audit log can attribute
                # each delivery to the rule that triggered it.
                if result.notifications and self._registry is not None:
                    from app.notifications.dispatcher import NotificationDispatcher

                    dispatcher = NotificationDispatcher(
                        session=self._session,
                        registry=self._registry,
                        event_bus=self._bus,
                    )
                    for notif in result.notifications:
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

            # Stage 9 (audit follow-up): apply quarantine action.
            # The aggregate carries quarantine=True if at least one
            # matched rule had a Quarantine or Delete action. We set
            # the row flag, stamp the timestamp, persist the reason,
            # and emit ``media.quarantined`` for any listening
            # consumers (the dashboard/files page invalidate via the
            # event bus). Quarantine is idempotent — re-evaluating
            # a row that's already quarantined doesn't bounce the
            # timestamp or re-emit.
            if aggregate.quarantine and not media_file.quarantined:
                media_file.quarantined = True
                media_file.quarantined_at = utcnow()
                media_file.quarantined_reason = aggregate.quarantine_reason
                if self._bus is not None:
                    await self._bus.emit(
                        "media.quarantined",
                        {
                            "id": media_file.id,
                            "path": media_file.path,
                            "reason": aggregate.quarantine_reason,
                        },
                        source="rules",
                    )

            # Stage 9 (audit follow-up): apply confirmed delete.
            # ``delete_paths`` is populated by ``Delete(confirm=True)``
            # actions. The default ``confirm=False`` falls through to
            # the quarantine branch above (soft-delete). When confirm
            # is true, move the file to ``data_dir/trash/`` and remove
            # the row. Filesystem failures here are logged but do NOT
            # crash the evaluation pipeline — the rule's other effects
            # (severity, tags, notifications) have already landed.
            if aggregate.delete_paths:
                await self._hard_delete_media(media_file)

            await self._session.flush()

        return FileOutcome(
            media_file_id=media_file.id,
            severity=aggregate.severity or "ok",
            severity_rank=aggregate.severity_rank,
            add_tags=list(aggregate.add_tags),
            matched_rule_ids=matched_rule_ids,
        )

    async def _hard_delete_media(self, media_file: MediaFile) -> None:
        """Move ``media_file`` to the trash directory and remove the
        ``MediaFile`` row. Filesystem failures are logged but do not
        crash the rules pipeline."""
        import shutil
        from pathlib import Path

        from app.core.settings import get_settings

        settings = get_settings()
        # ``data_dir`` is the configured runtime data path. We carve a
        # ``trash`` subdirectory there so a misconfigured rule is
        # always recoverable — the operator can move the file back.
        trash_root = Path(settings.data_dir) / "trash"
        try:
            trash_root.mkdir(parents=True, exist_ok=True)
            src = Path(media_file.path)
            if src.exists():
                # Avoid collisions: include the media_file.id in the
                # destination filename so two files of the same name
                # in different libraries don't overwrite each other.
                dst = trash_root / f"{media_file.id}__{src.name}"
                shutil.move(str(src), str(dst))
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

        if self._bus is not None:
            await self._bus.emit(
                "media.deleted",
                {"id": media_file.id, "path": media_file.path},
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
