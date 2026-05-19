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
from typing import Any

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
            # Drop stale evaluation rows for rules that no longer
            # match — but only for rules included in *this* pass.
            # When the caller hands us a subset (e.g. ``evaluate_rule``
            # firing one rule against every file), the rows for rules
            # that weren't evaluated must be left untouched; otherwise
            # a single-rule re-run would wipe every other rule's
            # history on those files.
            evaluated_rule_ids = [rule.id for rule, _ in rules]
            if evaluated_rule_ids:
                matched_set = set(matched_rule_ids)
                stale = await self._session.execute(
                    select(RuleEvaluation).where(
                        RuleEvaluation.media_file_id == media_file.id,
                        RuleEvaluation.rule_id.in_(evaluated_rule_ids),
                    )
                )
                for row in stale.scalars().all():
                    if row.rule_id not in matched_set:
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

            # v1.9 Stage 4.6 — VT lookup as a rule action.
            # When any matching rule's ``vt_lookup`` action fires,
            # the aggregate carries the flag; we enqueue the file
            # for the VT plugin's worker. The enqueue is idempotent
            # on ``media_file_id`` (the vt_queue table has a unique
            # constraint), so multiple rules matching on the same
            # file still result in one queue row. Same write
            # target as the scanner's auto-enqueue path — the VT
            # worker treats them identically once queued.
            #
            # The local import mirrors the scanner's pattern: keeps
            # the rules layer free of a hard dependency on the VT
            # plugin (the plugin can be absent from installs that
            # don't carry it).
            if aggregate.vt_lookup_requested:
                try:
                    from plugins.virustotal.backend import (
                        enqueue_for_vt_lookup,
                    )

                    await enqueue_for_vt_lookup(
                        self._session, media_file_id=media_file.id
                    )
                except ImportError:
                    # VT plugin not present — operator authored a
                    # rule with a vt_lookup action on an install
                    # without the plugin. Log + continue rather
                    # than failing the whole rule evaluation.
                    log.warning(
                        "rule.vt_lookup_action_plugin_missing",
                        media_file_id=media_file.id,
                    )

            # v1.9 Stage 5.1 — search_upstream rule action. The
            # aggregate carries a list of {target, integration_id}
            # dicts (one per matched ``search_upstream`` action).
            # Dedupe by (integration_id, media_file_id) so
            # multiple rules requesting the same search on the
            # same file fire it once. The actual upstream call
            # happens inline — same shape as the existing inline
            # actions (delete, vt_lookup). Audit + WS events emit
            # per (integration_id, file, status).
            if aggregate.search_upstream_requests:
                await self._trigger_upstream_searches(
                    media_file, aggregate.search_upstream_requests
                )

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

    # ── v1.9 Stage 5.1 — search_upstream rule action ─────────────
    async def _trigger_upstream_searches(
        self,
        media_file: MediaFile,
        requests: list[dict[str, str]],
    ) -> None:
        """Dispatch a search trigger to each requested integration.

        ``requests`` is a list of ``{"target": str, "integration_id":
        str}`` dicts as accumulated by the evaluator. We dedupe by
        ``integration_id`` (multiple rules matching the same file
        with the same integration → one upstream call), then per
        unique integration we:

          1. Load the Integration row. Skip + log if missing or
             disabled — operator may have disabled the integration
             since the rule was authored; we don't want a rule
             pinned to a removed integration to error every scan.
          2. Resolve the provider via the registry. Skip + log if
             unregistered (operator removed the plugin).
          3. Confirm the resolved provider's kind matches the rule's
             declared ``target``. Mismatch logs a warning and skips
             — typically means the operator moved an integration to
             a different kind, which is degenerate but recoverable.
          4. Call ``provider.trigger_search(config, media_file.path)``
             via ``hasattr`` to gracefully skip providers without
             the method (older plugin versions).
          5. Persist an audit log entry tagged
             ``rule.action.search_upstream`` with the outcome.
          6. Emit a ``rule.action.search_upstream`` bus event so
             the UI can surface activity live.

        Exceptions from individual providers are caught and
        converted to status=error rows in the audit log — one
        broken integration must not abort the rule pipeline for
        the file (or for the rest of the matched integrations).
        """
        from app.integrations.types import SearchTriggerResult
        from app.models.integration import Integration
        from app.services.audit_service import AuditService

        # Dedupe by integration_id. Preserve the first-seen target
        # so the audit entry reflects what the operator actually
        # wrote (the target is also re-validated against the
        # resolved integration's kind below).
        seen_integration_ids: set[str] = set()
        unique_requests: list[dict[str, str]] = []
        for req in requests:
            iid = req.get("integration_id") or ""
            if not iid or iid in seen_integration_ids:
                continue
            seen_integration_ids.add(iid)
            unique_requests.append(req)

        audit = AuditService(self._session)

        for req in unique_requests:
            integration_id = req["integration_id"]
            target = req.get("target") or ""

            # 1. Load the integration row.
            integration = await self._session.get(Integration, integration_id)
            if integration is None:
                log.warning(
                    "rule.search_upstream.integration_missing",
                    integration_id=integration_id,
                    media_file_id=media_file.id,
                )
                await self._emit_search_upstream_audit(
                    audit,
                    media_file=media_file,
                    integration_id=integration_id,
                    target=target,
                    result=SearchTriggerResult(
                        status="error",
                        detail="Integration not found",
                    ),
                )
                continue
            if not integration.enabled:
                log.info(
                    "rule.search_upstream.integration_disabled",
                    integration_id=integration_id,
                    media_file_id=media_file.id,
                )
                await self._emit_search_upstream_audit(
                    audit,
                    media_file=media_file,
                    integration_id=integration_id,
                    target=target,
                    result=SearchTriggerResult(
                        status="error",
                        detail="Integration disabled",
                    ),
                )
                continue

            # 2. Resolve the provider.
            if self._registry is None:
                # No registry on a dry-run/test path. Skip silently
                # rather than logging an error — these contexts
                # don't ship audit log entries either.
                continue
            provider = self._provider_for_integration(integration)
            if provider is None:
                log.warning(
                    "rule.search_upstream.provider_unregistered",
                    integration_id=integration_id,
                    kind=integration.kind,
                )
                await self._emit_search_upstream_audit(
                    audit,
                    media_file=media_file,
                    integration_id=integration_id,
                    target=target,
                    result=SearchTriggerResult(
                        status="error",
                        detail=f"No provider registered for kind {integration.kind!r}",
                    ),
                )
                continue

            # 3. Target / kind sanity check.
            if target and target != integration.kind:
                log.warning(
                    "rule.search_upstream.target_kind_mismatch",
                    integration_id=integration_id,
                    rule_target=target,
                    integration_kind=integration.kind,
                )
                await self._emit_search_upstream_audit(
                    audit,
                    media_file=media_file,
                    integration_id=integration_id,
                    target=target,
                    result=SearchTriggerResult(
                        status="error",
                        detail=(
                            f"Rule target {target!r} doesn't match "
                            f"integration kind {integration.kind!r}"
                        ),
                    ),
                )
                continue

            # 4. Capability + call.
            if not hasattr(provider, "trigger_search"):
                log.info(
                    "rule.search_upstream.provider_no_trigger_search",
                    integration_id=integration_id,
                    kind=integration.kind,
                )
                await self._emit_search_upstream_audit(
                    audit,
                    media_file=media_file,
                    integration_id=integration_id,
                    target=target,
                    result=SearchTriggerResult(
                        status="error",
                        detail=(
                            f"{integration.kind!r} provider does not "
                            f"implement trigger_search"
                        ),
                    ),
                )
                continue

            config = self._build_integration_config(integration)
            try:
                result = await provider.trigger_search(
                    config, media_file.path
                )
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "rule.search_upstream.provider_exception",
                    integration_id=integration_id,
                    kind=integration.kind,
                    error=str(exc),
                )
                result = SearchTriggerResult(
                    status="error",
                    detail=f"Provider exception: {exc}",
                )

            # 5 + 6. Audit + WS emit.
            await self._emit_search_upstream_audit(
                audit,
                media_file=media_file,
                integration_id=integration_id,
                target=target or integration.kind,
                result=result,
            )

    async def _emit_search_upstream_audit(
        self,
        audit,
        *,
        media_file: MediaFile,
        integration_id: str,
        target: str,
        result,
    ) -> None:
        """Persist the audit log entry + emit the WS event for one
        search_upstream attempt. Both surfaces carry the same payload
        so the UI can render the Audit Log row and the "Recent
        activity" timeline from either source."""
        payload: dict[str, Any] = {
            "integration_id": integration_id,
            "media_file_id": media_file.id,
            "media_file_path": media_file.path,
            "target": target,
            "status": result.status,
            "upstream_id": result.upstream_id,
            "detail": result.detail,
        }
        try:
            await audit.record(
                action="rule.action.search_upstream",
                actor_id=None,
                actor_label="rules",
                target_type="media_file",
                target_id=media_file.id,
                metadata=payload,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "rule.search_upstream.audit_failed",
                integration_id=integration_id,
                media_file_id=media_file.id,
                error=str(exc),
            )
        if self._bus is not None:
            try:
                await self._bus.emit(
                    "rule.action.search_upstream",
                    payload,
                    source="rules",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "rule.search_upstream.bus_emit_failed",
                    integration_id=integration_id,
                    media_file_id=media_file.id,
                    error=str(exc),
                )

    def _provider_for_integration(self, integration):
        """Return the IntegrationProvider registered for
        ``integration.kind``, or None if unregistered.

        Mirrors ``IntegrationManager.provider_for`` but kept inline
        here so RulesService doesn't have to build a full manager
        (which would require a SecretBox we don't otherwise need).
        ``registry.providers_for`` returns a list because the
        plugin system allows multiple providers per capability;
        the first hit is canonical."""
        cap = f"integration.{integration.kind}"
        providers = self._registry.providers_for(cap) if self._registry else []
        return providers[0] if providers else None

    def _build_integration_config(self, integration):
        """Build an IntegrationConfig from a row. Decrypts the
        secrets dict via the global secret box.

        Lifted from ``IntegrationManager.build_config`` rather than
        instantiating a full manager — see the note on
        ``_provider_for_integration``. Decryption failures bubble
        as exceptions; the call site catches them and records an
        error audit row."""
        from app.integrations.types import IntegrationConfig
        from app.security.secrets import get_secret_box

        secrets: dict[str, Any] = {}
        if integration.secrets_ciphertext:
            box = get_secret_box()
            secrets = box.decrypt_dict(integration.secrets_ciphertext)
        return IntegrationConfig(
            integration_id=integration.id,
            name=integration.name,
            kind=integration.kind,
            options=dict(integration.config or {}),
            secrets=secrets,
        )

    # ── Bulk evaluation ──────────────────────────────────────────
    async def evaluate_rule(self, rule_id: str) -> int:
        """v1.9 OP-15 — run a SINGLE rule against every file in
        every library.

        Operator workflow: after creating or editing a rule (esp.
        one with a ``vt_lookup`` action), the operator wants to
        see it fire against the existing library without a full
        all-rules re-evaluation. This method is the targeted
        path — it loads only the named rule, walks every library,
        and runs the file → outcome pipeline for each. Tags,
        severity updates, VT queue inserts, and search_upstream
        actions all fire as they would in a full re-evaluation
        because we use the standard ``evaluate_file`` entry point;
        just with a single-rule rule list.

        Returns the total file count examined across all
        libraries.
        """
        rule = await self._rules.get(rule_id)
        if rule is None:
            raise ValueError(f"Rule {rule_id!r} not found")
        if not rule.enabled:
            # Evaluating a disabled rule is almost certainly
            # operator error — the rule wouldn't fire on the
            # automatic path either. Surface a clear error
            # rather than silently doing nothing.
            raise ValueError(
                f"Rule {rule_id!r} is disabled. Enable it before "
                "running a targeted evaluation."
            )

        # Parse the rule's definition once.
        from app.rules.schema import RuleDefinition

        try:
            definition = RuleDefinition.model_validate(rule.definition)
        except Exception as exc:
            raise ValueError(
                f"Rule {rule_id!r} has an invalid definition: {exc}"
            ) from exc

        single_rule_list: list[tuple[Rule, RuleDefinition]] = [
            (rule, definition)
        ]

        # Walk every library.
        from app.models.library import Library

        library_rows = (
            await self._session.execute(select(Library))
        ).scalars().all()

        total_examined = 0
        now = utcnow()
        match_count = 0
        for lib in library_rows:
            filt = MediaFilter(library_id=lib.id)
            page = await self._media.list(filt=filt, offset=0, limit=10_000)
            for media_file in page.items:
                outcome = await self.evaluate_file(
                    media_file, single_rule_list
                )
                if rule.id in outcome.matched_rule_ids:
                    match_count += 1
                total_examined += 1

        # Update rule-level tracking for the freshly-run rule.
        rule.last_evaluated_at = now
        rule.last_match_count = match_count
        await self._session.flush()

        return total_examined

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

    async def evaluate_all_libraries(self) -> tuple[int, int]:
        """Re-evaluate every file in every library against all enabled rules.

        Returns ``(libraries_evaluated, files_evaluated)``. Walks
        libraries server-side so the operator gets one HTTP call and
        one transaction surface; per-library bookkeeping (last_evaluated_at,
        last_match_count) still happens via ``evaluate_library``.
        """
        from app.models.library import Library

        library_rows = (
            await self._session.execute(select(Library))
        ).scalars().all()
        files_total = 0
        for lib in library_rows:
            files_total += await self.evaluate_library(lib.id)
        return len(library_rows), files_total

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
