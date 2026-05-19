"""Rules router (``/api/v1/rules``)."""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, ValidationError as PydanticValidationError

from app.api.auth_deps import AdminUser, CurrentUser
from app.api.dependencies import EventBusDep, RegistryDep, SessionDep
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.rule import Rule
from app.rules.evaluator import EvaluationResult, evaluate
from app.rules.schema import (
    ARRAY_FIELDS,
    BOOL_FIELDS,
    BOOL_OPS,
    NUMERIC_FIELDS,
    NUMERIC_OPS,
    SEVERITY_LEVELS,
    SET_OPS,
    STRING_OPS,
    SUPPORTED_FIELDS,
    RuleDefinition,
)
from app.schemas.rule_suggestion import (
    AnalyzePlaybackRunResponse,
    RuleSuggestionRead,
    SuggestionDeployRequest,
    SuggestionDismissRequest,
)
from app.schemas.rules import (
    RuleCreate,
    RuleDryRunRequest,
    RuleDryRunResponse,
    RuleEvaluateAllLibrariesResponse,
    RuleEvaluateLibraryResponse,
    RuleEvaluateRuleResponse,
    RuleEvaluationFileRow,
    RuleEvaluationRead,
    RuleExportBundle,
    RuleExportEntry,
    RuleImportOutcome,
    RuleImportRequest,
    RuleImportResponse,
    RuleRead,
    RuleUpdate,
    RuleVocabularyAction,
    RuleVocabularyField,
    RuleVocabularyRead,
)
from app.services.repositories import (
    MediaRepository,
    RuleEvaluationRepository,
    RuleRepository,
)
from app.services.rules_service import RulesService

router = APIRouter(prefix="/rules", tags=["rules"])


def _validate_definition(definition: dict) -> RuleDefinition:
    try:
        return RuleDefinition.model_validate(definition)
    except PydanticValidationError as exc:
        # Pydantic embeds the underlying exception object in error['ctx'];
        # stringify it so the JSON error envelope can serialize cleanly.
        errors = []
        for err in exc.errors(include_url=False):
            entry = dict(err)
            if "ctx" in entry and isinstance(entry["ctx"], dict):
                entry["ctx"] = {
                    k: str(v) if isinstance(v, BaseException) else v
                    for k, v in entry["ctx"].items()
                }
            errors.append(entry)
        raise ValidationError(
            "Rule definition is invalid",
            details={"errors": errors},
        ) from exc


@router.get(
    "/vocabulary",
    response_model=RuleVocabularyRead,
    summary="Field, operator, and action vocabulary for the visual builder",
)
async def vocabulary(_user: CurrentUser) -> RuleVocabularyRead:
    """Stage 15: everything the visual rule builder needs in one call.

    The builder mounts this once and uses the response to render typed
    condition rows (a string field gets a text input + string ops; a
    numeric field gets a number input + numeric ops, etc.). Kept on a
    single endpoint rather than split per-type so the frontend never
    has to coordinate multiple loading states.
    """
    # Field display labels — turn ``video_codec`` into ``Video codec``
    # and special-case the ones that don't autoformat well.
    def _label(key: str) -> str:
        special = {
            "bitrate_kbps": "Bitrate (kbps)",
            "size_bytes": "Size (bytes)",
            "duration_seconds": "Duration (s)",
            "has_subtitles": "Has subtitles",
            "is_orphaned": "Orphaned",
        }
        if key in special:
            return special[key]
        return key.replace("_", " ").capitalize()

    def _type_for(key: str) -> str:
        if key in NUMERIC_FIELDS:
            return "numeric"
        if key in BOOL_FIELDS:
            return "bool"
        if key in ARRAY_FIELDS:
            return "array"
        return "string"

    # A handful of string fields have a finite known value set; surfacing
    # that lets the builder render a dropdown rather than a free-text
    # input. We hard-code the ones we know about; other strings stay
    # free-form.
    #
    # Stage 06 (v1.7) added ``vt_status`` to ``SUPPORTED_FIELDS`` with
    # a fixed literal value set (per addendum B.4); surface those
    # values here so the builder renders the right dropdown.
    from app.rules.schema import VT_STATUS_VALUES

    enums: dict[str, list[str]] = {
        "category": ["media", "subtitle", "image", "metadata", "junk", "unknown"],
        "vt_status": sorted(VT_STATUS_VALUES),
    }

    fields_out: list[RuleVocabularyField] = []
    for key in sorted(SUPPORTED_FIELDS):
        fields_out.append(
            RuleVocabularyField(
                key=key,
                label=_label(key),
                type=_type_for(key),
                enum=enums.get(key),
            )
        )

    ops_by_type: dict[str, list[str]] = {
        "numeric": sorted(NUMERIC_OPS),
        "string": sorted(STRING_OPS),
        "bool": sorted(BOOL_OPS),
        "array": sorted(SET_OPS),
    }

    actions_out = [
        RuleVocabularyAction(
            type="set_severity",
            label="Set severity",
            args_schema={
                "severity": {
                    "type": "string",
                    "enum": list(SEVERITY_LEVELS.keys()),
                    "required": True,
                }
            },
        ),
        RuleVocabularyAction(
            type="add_tag",
            label="Add tag",
            args_schema={
                "tag": {"type": "string", "minLength": 1, "maxLength": 64, "required": True}
            },
        ),
        RuleVocabularyAction(
            type="queue_optimization",
            label="Queue optimization",
            args_schema={
                "profile": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "required": True,
                    "hint": "Optimization profile name (configure under Optimization)",
                }
            },
        ),
        RuleVocabularyAction(
            type="notify",
            label="Notify",
            args_schema={
                "channel": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "required": True,
                    "hint": "Notification channel name (configure under Notifications)",
                },
                "message": {
                    "type": "string",
                    "maxLength": 512,
                    "required": False,
                },
                # Stage 06 (v1.7): optional notification throttle.
                # Per plan §352 + the schema's ``NotifyThrottle``
                # model: window_seconds >= 60, max_per_window >= 1.
                # The builder renders this as a collapsed "Throttle"
                # section that expands into two numeric inputs.
                "throttle": {
                    "type": "object",
                    "required": False,
                    "hint": (
                        "Cap deliveries to N per rolling window. "
                        "Beyond the cap, the rule emits "
                        "``rule.throttled`` and audit-logs one "
                        "summary entry per (rule, window)."
                    ),
                    "properties": {
                        "window_seconds": {
                            "type": "numeric",
                            "minimum": 60,
                            "required": True,
                            "hint": "Window length (≥ 60 seconds)",
                        },
                        "max_per_window": {
                            "type": "numeric",
                            "minimum": 1,
                            "required": True,
                            "hint": (
                                "Max notifications inside the window "
                                "(≥ 1; use 0 by disabling the rule)"
                            ),
                        },
                    },
                },
            },
        ),
        # Stage 9 (audit follow-up), updated Stage 05 (v1.7): Stage
        # 05 retired the Quarantine action entirely (Section A.0 —
        # "delete means delete") and dropped Delete's ``confirm``
        # flag. The Delete action is now unconditional; the
        # operator-supplied ``reason`` flows to the audit log entry
        # the service emits for every successful delete.
        RuleVocabularyAction(
            type="delete",
            label="Delete",
            args_schema={
                "reason": {
                    "type": "string",
                    "maxLength": 256,
                    "required": False,
                    "hint": (
                        "Optional human-readable reason recorded in "
                        "the audit log when the rule deletes a file. "
                        "Recommended so operators reviewing the audit "
                        "trail see WHY a file was removed."
                    ),
                },
            },
        ),
        # v1.9 Stage 4.6: VT lookup. No params today (the action
        # ``extra="forbid"``s any keys), but surfacing it in the
        # builder so the operator can pick it from the action
        # dropdown rather than hand-editing JSON.
        RuleVocabularyAction(
            type="vt_lookup",
            label="VirusTotal lookup",
            args_schema={},
        ),
        # v1.9 Stage 5.1: cross-integration search trigger.
        # ``target`` is an enum (sonarr / radarr / bazarr) so the
        # builder renders it as a select. ``integration_id`` is a
        # special string with a hint flagging it for the special
        # integration-picker treatment in the frontend; the
        # frontend filters its integration list by ``target``.
        RuleVocabularyAction(
            type="search_upstream",
            label="Search in upstream",
            args_schema={
                "target": {
                    "type": "string",
                    "enum": ["sonarr", "radarr", "bazarr"],
                    "required": True,
                    "hint": (
                        "Upstream service kind. The integration "
                        "picker below filters to enabled "
                        "integrations of this kind."
                    ),
                },
                "integration_id": {
                    "type": "string",
                    "required": True,
                    "format": "integration_picker",
                    "hint": (
                        "Pick which configured integration row "
                        "to call. Operators with multiple Sonarr "
                        "instances must pick one explicitly."
                    ),
                },
            },
        ),
    ]

    return RuleVocabularyRead(
        fields=fields_out,
        ops=ops_by_type,
        severities=list(SEVERITY_LEVELS.keys()),
        actions=actions_out,
        # Stage 06 (v1.7): rule-level flags the builder must
        # surface. Today only the destructive-action ack flag
        # (addendum A.0.1). Frontend renders it as a checkbox
        # whose visibility is gated by whether any action in
        # the rule's actions list is type=delete.
        rule_flags={
            "acknowledged_destructive": {
                "type": "bool",
                "label": "I understand this rule deletes files from disk.",
                "required_when": {"any_action_type": "delete"},
                "hint": (
                    "Auditarr's defensive layer for destructive rules. "
                    "A rule containing a 'delete' action will not save "
                    "without this acknowledgement. The flag is forbidden "
                    "on rules that don't contain a delete action."
                ),
            },
        },
    )


# ── Stage 16 Turn 2: rule suggestions ─────────────────────────
@router.get(
    "/suggestions",
    response_model=list[RuleSuggestionRead],
    summary="Pending data-driven rule suggestions",
)
async def list_suggestions(
    _user: CurrentUser,
    session: SessionDep,
) -> list[RuleSuggestionRead]:
    """The dashboard "Rule suggestions" card consumes this. Returns
    only ``status=pending`` suggestions ordered by confidence
    descending; deployed and dismissed entries are filtered out."""
    from app.services.repositories import RuleSuggestionRepository

    repo = RuleSuggestionRepository(session)
    rows = await repo.list_pending()
    return [RuleSuggestionRead.model_validate(r) for r in rows]


# v1.10 — AI provider usage summary. Surfaces per-integration
# call counts in the rolling 24h window vs the configured
# ``daily_call_budget``. Registered BEFORE the
# ``/suggestions/{suggestion_id}`` wildcard so the static path
# wins the FastAPI route match — otherwise ``ai-usage`` would
# be captured as a suggestion id and produce a 404.
@router.get(
    "/suggestions/ai-usage",
    summary="Per-integration AI call counts vs configured budget",
)
async def ai_usage_summary(
    _admin: AdminUser,
    session: SessionDep,
) -> dict[str, object]:
    """Returns one entry per enabled AI-provider integration with
    the count of AI suggestion calls in the last 24 hours, the
    configured daily budget, the remaining headroom, and whether
    the budget has been exceeded.

    The window is rolling-24h (not calendar-day). Operators with
    a calendar-day expectation see the same data; the difference
    only matters within 24h of a budget-exceeding burst.
    """
    from app.models.integration import Integration as _Integration
    from app.services.ai.suggestions import AISuggestionService
    from sqlalchemy import select

    integrations = (
        await session.execute(
            select(_Integration).where(
                _Integration.kind == "ai-provider"
            )
        )
    ).scalars().all()

    service = AISuggestionService(session=session)
    rows = []
    for ig in integrations:
        if not ig.enabled:
            continue
        rows.append(await service.usage_summary(ig))

    return {"integrations": rows}


# v1.9 Stage 9.2 — stale rule suggestions. Registered BEFORE the
# ``/suggestions/{suggestion_id}`` wildcard for the same reason
# as ``/suggestions/ai-usage`` above: otherwise FastAPI matches
# "stale" as a suggestion_id and the endpoint 404s.
@router.get(
    "/suggestions/stale",
    summary="Active rules that look stale (inactive or overzealous)",
)
async def list_stale_rule_suggestions(
    _user: CurrentUser,
    session: SessionDep,
) -> dict[str, object]:
    """v1.9 Stage 9.2 — rule-removal / severity-lowering hints.

    Surfaces two kinds of observations:

      * ``inactive`` — the rule was evaluated recently but
        matched zero files. Likely a candidate for
        disable / delete.
      * ``overzealous`` — the rule fires AND most observed
        playback in the analysis window was direct_play.
        Consider lowering severity rather than removing.

    Read-only: this endpoint returns observations only. The
    operator decides what to do via the existing rule edit /
    delete surfaces."""
    from app.services.playback.stale_rule_analyzer import StaleRuleAnalyzer

    analyzer = StaleRuleAnalyzer(session=session)
    outcome = await analyzer.analyze()
    return {
        "suggestions": [s.to_dict() for s in outcome.suggestions],
        "rules_examined": outcome.rules_examined,
    }


@router.get(
    "/suggestions/{suggestion_id}",
    response_model=RuleSuggestionRead,
    summary="Suggestion detail (used by the review modal)",
)
async def get_suggestion(
    suggestion_id: str,
    _user: CurrentUser,
    session: SessionDep,
) -> RuleSuggestionRead:
    from app.services.repositories import RuleSuggestionRepository

    suggestion = await RuleSuggestionRepository(session).get(suggestion_id)
    if suggestion is None:
        raise NotFoundError(
            "Suggestion not found", details={"id": suggestion_id}
        )
    return RuleSuggestionRead.model_validate(suggestion)


@router.post(
    "/suggestions/{suggestion_id}/deploy",
    response_model=RuleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Deploy a suggestion as a real rule",
)
async def deploy_suggestion(
    suggestion_id: str,
    body: SuggestionDeployRequest,
    _admin: AdminUser,
    session: SessionDep,
) -> RuleRead:
    """Create a :class:`Rule` from a suggestion's definition and mark
    the suggestion as deployed. The operator may pass overrides for
    name / description / priority / enabled, plus a tweaked
    ``definition`` JSON if they edited it in the visual builder."""
    from app.services.repositories import (
        RuleRepository,
        RuleSuggestionRepository,
    )

    suggestions = RuleSuggestionRepository(session)
    rules = RuleRepository(session)

    suggestion = await suggestions.get(suggestion_id)
    if suggestion is None:
        raise NotFoundError(
            "Suggestion not found", details={"id": suggestion_id}
        )
    if suggestion.status != "pending":
        raise ConflictError(
            f"Suggestion is {suggestion.status}, cannot deploy",
            details={"id": suggestion_id, "status": suggestion.status},
        )

    # Validate any overridden definition against the rule schema.
    final_definition = body.definition or suggestion.definition
    _validate_definition(final_definition)

    rule_name = body.name or suggestion.name
    # Suggestions may share names with each other (e.g. two HEVC
    # suggestions) — defensively ensure uniqueness.
    if await rules.get_by_name(rule_name):
        rule_name = f"{rule_name} ({_dt_now_short()})"

    rule = Rule(
        name=rule_name,
        description=body.description
        or f"Deployed from suggestion ({suggestion.heuristic})",
        enabled=body.enabled if body.enabled is not None else True,
        priority=body.priority if body.priority is not None else 100,
        definition=final_definition,
    )
    await rules.add(rule)

    suggestion.status = "deployed"
    suggestion.deployed_rule_id = rule.id
    suggestion.deployed_at = _dt_utcnow()

    await session.commit()
    return RuleRead.model_validate(rule)


@router.post(
    "/suggestions/{suggestion_id}/dismiss",
    response_model=RuleSuggestionRead,
    summary="Dismiss a suggestion (sticky for 30 days)",
)
async def dismiss_suggestion(
    suggestion_id: str,
    body: SuggestionDismissRequest,
    _admin: AdminUser,
    session: SessionDep,
) -> RuleSuggestionRead:
    from app.services.repositories import RuleSuggestionRepository

    suggestions = RuleSuggestionRepository(session)
    suggestion = await suggestions.get(suggestion_id)
    if suggestion is None:
        raise NotFoundError(
            "Suggestion not found", details={"id": suggestion_id}
        )
    if suggestion.status == "deployed":
        raise ConflictError(
            "Suggestion already deployed", details={"id": suggestion_id}
        )
    suggestion.status = "dismissed"
    suggestion.dismissed_at = _dt_utcnow()
    suggestion.dismissed_reason = body.reason
    await session.commit()
    return RuleSuggestionRead.model_validate(suggestion)


@router.post(
    "/analyze-playback/run",
    response_model=AnalyzePlaybackRunResponse,
    summary="Run the playback analyzer right now (admin)",
)
async def run_analyzer(
    _admin: AdminUser,
    session: SessionDep,
) -> AnalyzePlaybackRunResponse:
    """Manual trigger for the analyzer that the cron normally runs
    daily. Useful for debugging fresh installs and for the smoke test
    that runs after setting up playback telemetry."""
    from app.services.playback import PlaybackAnalyzer

    analyzer = PlaybackAnalyzer(session=session)
    outcome = await analyzer.analyze()
    return AnalyzePlaybackRunResponse(
        examined_events=outcome.examined_events,
        candidates_generated=outcome.candidates_generated,
        suggestions_created=outcome.suggestions_created,
        skipped_deduped=outcome.skipped_deduped,
        skipped_dismissed=outcome.skipped_dismissed,
        skipped_deployed=outcome.skipped_deployed,
        skipped_too_few_events=outcome.skipped_too_few_events,
        # Stage 09 (plan §482) — surface the count split so the
        # recommendation card shows the true total and a
        # path-mapping hint when applicable.
        examined_events_total=outcome.examined_events_total,
        examined_events_resolved=outcome.examined_events_resolved,
        examined_events_unresolved=outcome.examined_events_unresolved,
    )


def _dt_utcnow():
    from app.utils.datetime import utcnow

    return utcnow()


def _dt_now_short() -> str:
    from app.utils.datetime import utcnow

    return utcnow().strftime("%Y%m%d-%H%M%S")


@router.get("", response_model=list[RuleRead], summary="List rules")
async def list_rules(
    _user: CurrentUser,
    session: SessionDep,
    is_builtin: bool | None = None,
) -> list[RuleRead]:
    """List rules, optionally filtered by origin.

    Stage 29 ``is_builtin`` filter:
      - ``None`` (default): return everything (custom + builtin)
      - ``True``: only builtins — used by the "Built-in" tab
      - ``False``: only custom — used by the "Custom" tab
    """
    rules = await RuleRepository(session).list_all()
    if is_builtin is not None:
        rules = [r for r in rules if r.is_builtin == is_builtin]
    return [RuleRead.model_validate(r) for r in rules]


@router.post(
    "",
    response_model=RuleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a rule",
)
async def create_rule(
    body: RuleCreate, _admin: AdminUser, session: SessionDep
) -> RuleRead:
    _validate_definition(body.definition)
    repo = RuleRepository(session)
    if await repo.get_by_name(body.name):
        raise ConflictError("A rule with that name already exists")
    rule = Rule(
        name=body.name,
        description=body.description,
        enabled=body.enabled,
        priority=body.priority,
        definition=body.definition,
    )
    await repo.add(rule)
    return RuleRead.model_validate(rule)


@router.get("/{rule_id}", response_model=RuleRead, summary="Get a rule")
async def get_rule(
    rule_id: str, _user: CurrentUser, session: SessionDep
) -> RuleRead:
    rule = await RuleRepository(session).get(rule_id)
    if rule is None:
        raise NotFoundError("Rule not found")
    return RuleRead.model_validate(rule)


@router.patch("/{rule_id}", response_model=RuleRead, summary="Update a rule")
async def update_rule(
    rule_id: str,
    body: RuleUpdate,
    _admin: AdminUser,
    session: SessionDep,
) -> RuleRead:
    repo = RuleRepository(session)
    rule = await repo.get(rule_id)
    if rule is None:
        raise NotFoundError("Rule not found")

    # Stage 29: built-in rules are codebase-owned. Operators can
    # toggle ``enabled`` and adjust ``priority`` (per-installation
    # tuning), but cannot rename, edit the body, or change the
    # description. Any attempt to touch the codebase-owned fields
    # returns 422 with a clear message; the rest of the patch is
    # rejected wholesale rather than partially applied so the
    # operator can correct the request and retry.
    if rule.is_builtin:
        forbidden_fields = []
        if body.name is not None and body.name != rule.name:
            forbidden_fields.append("name")
        if (
            body.description is not None
            and body.description != rule.description
        ):
            forbidden_fields.append("description")
        if body.definition is not None:
            forbidden_fields.append("definition")
        if forbidden_fields:
            raise ValidationError(
                "Cannot edit built-in rule fields: "
                + ", ".join(forbidden_fields)
                + ". Duplicate the rule to create a writable custom copy.",
                details={"forbidden_fields": forbidden_fields},
            )

    if body.definition is not None:
        _validate_definition(body.definition)
        rule.definition = body.definition
    if body.name is not None:
        rule.name = body.name
    if body.description is not None:
        rule.description = body.description
    if body.enabled is not None:
        rule.enabled = body.enabled
    if body.priority is not None:
        rule.priority = body.priority
    await session.flush()
    return RuleRead.model_validate(rule)


@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a rule (and its evaluation history)",
)
async def delete_rule(
    rule_id: str, _admin: AdminUser, session: SessionDep
) -> None:
    repo = RuleRepository(session)
    rule = await repo.get(rule_id)
    if rule is None:
        raise NotFoundError("Rule not found")
    # Stage 29: built-in rules can't be deleted — they'd come back
    # on the next startup anyway. The operator-facing answer is
    # "disable it" via PATCH /enabled=false.
    if rule.is_builtin:
        raise ValidationError(
            "Cannot delete a built-in rule. Disable it instead.",
            details={"rule_id": rule_id, "rule_name": rule.name},
        )
    await repo.delete(rule)


@router.get(
    "/{rule_id}/evaluations",
    response_model=list[RuleEvaluationRead],
    summary="Recent evaluation matches for this rule",
)
async def list_rule_evaluations(
    rule_id: str,
    _user: CurrentUser,
    session: SessionDep,
    limit: int = 50,
) -> list[RuleEvaluationRead]:
    repo = RuleEvaluationRepository(session)
    return [
        RuleEvaluationRead.model_validate(row)
        for row in await repo.list_for_rule(rule_id, limit=limit)
    ]


@router.get(
    "/{rule_id}/matched-files",
    response_model=list[RuleEvaluationFileRow],
    summary="Stage 14b: files this rule has matched, with path joined",
)
async def list_rule_matched_files(
    rule_id: str,
    _user: CurrentUser,
    session: SessionDep,
    limit: int = 200,
) -> list[RuleEvaluationFileRow]:
    """Stage 14b (audit follow-up): backs the per-rule "Matched
    files" tab in the rule editor. Returns a lightweight, file-
    joined row per evaluation — enough to render filename + severity
    in a table and click-through to the Files page drawer.

    Returns 404 when the rule does not exist; an empty array when
    the rule exists but has zero evaluations (distinct cases — the
    UI handles them differently)."""
    rule = await RuleRepository(session).get(rule_id)
    if rule is None:
        raise NotFoundError("Rule not found")
    repo = RuleEvaluationRepository(session)
    summaries = await repo.list_for_rule_with_files(rule_id, limit=limit)
    return [RuleEvaluationFileRow.model_validate(s) for s in summaries]


@router.post(
    "/dry-run",
    response_model=RuleDryRunResponse,
    summary="Evaluate a candidate rule definition against an existing file",
)
async def dry_run(
    body: RuleDryRunRequest,
    _user: CurrentUser,
    session: SessionDep,
) -> RuleDryRunResponse:
    definition = _validate_definition(body.definition)
    media = await MediaRepository(session).get(body.media_file_id)
    if media is None:
        raise NotFoundError("Media file not found")
    service = RulesService(session=session)
    eval_input = await service.build_input(media)
    result: EvaluationResult = evaluate(definition, eval_input)
    return RuleDryRunResponse(
        matched=result.matched,
        severity=result.severity,
        severity_rank=result.severity_rank,
        add_tags=result.add_tags,
        queue_optimizations=result.queue_optimizations,
    )


@router.post(
    "/libraries/{library_id}/evaluate",
    response_model=RuleEvaluateLibraryResponse,
    summary="Re-evaluate every file in a library against all enabled rules",
)
async def evaluate_library(
    library_id: str,
    _admin: AdminUser,
    session: SessionDep,
    bus: EventBusDep,
    registry: RegistryDep,
) -> RuleEvaluateLibraryResponse:
    service = RulesService(session=session, event_bus=bus, registry=registry)
    count = await service.evaluate_library(library_id)
    return RuleEvaluateLibraryResponse(
        library_id=library_id, files_evaluated=count
    )


@router.post(
    "/libraries/evaluate-all",
    response_model=RuleEvaluateAllLibrariesResponse,
    summary="Re-evaluate every file in every library against all enabled rules",
)
async def evaluate_all_libraries(
    _admin: AdminUser,
    session: SessionDep,
    bus: EventBusDep,
    registry: RegistryDep,
) -> RuleEvaluateAllLibrariesResponse:
    service = RulesService(session=session, event_bus=bus, registry=registry)
    libs, files = await service.evaluate_all_libraries()
    return RuleEvaluateAllLibrariesResponse(
        libraries_evaluated=libs, files_evaluated=files
    )


# v1.9 OP-15 — targeted re-evaluation of a single rule across
# every library.
@router.post(
    "/{rule_id}/evaluate-now",
    response_model=RuleEvaluateRuleResponse,
    summary="Re-evaluate this specific rule against every file in every library",
)
async def evaluate_rule_now(
    rule_id: str,
    _admin: AdminUser,
    session: SessionDep,
    bus: EventBusDep,
    registry: RegistryDep,
) -> RuleEvaluateRuleResponse:
    """v1.9 OP-15 — operator-facing "fire this rule now" trigger.

    Use case: operator creates or edits a rule (especially one
    with a ``vt_lookup`` or ``search_upstream`` action) and wants
    to see it fire against existing library files immediately,
    without running the full all-rules evaluation. Targeted: just
    this one rule, just the files it would match.

    Returns the total file count examined across all libraries —
    the actual match count is visible via the rule's
    ``last_match_count`` field after the call returns.

    Errors:
      * 404 — rule not found
      * 400 — rule is disabled or has an invalid definition
    """
    service = RulesService(session=session, event_bus=bus, registry=registry)
    try:
        count = await service.evaluate_rule(rule_id)
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise NotFoundError(msg) from exc
        raise ValidationError(msg) from exc
    return RuleEvaluateRuleResponse(
        rule_id=rule_id, files_evaluated=count
    )


# ── Stage 24: duplicate / export / import ─────────────────────


@router.post(
    "/{rule_id}/duplicate",
    response_model=RuleRead,
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate an existing rule",
)
async def duplicate_rule(
    rule_id: str,
    _admin: AdminUser,
    session: SessionDep,
) -> RuleRead:
    """Create a copy of an existing rule with a guaranteed-unique
    name. The copy is created disabled so it can be reviewed before
    becoming live — duplicating to immediately diverge from the
    original is the only reason an operator does this, and shipping
    the divergent rule in enabled state without inspection would be
    the failure mode we want to prevent. The original is untouched.
    """
    repo = RuleRepository(session)
    rule = await repo.get(rule_id)
    if rule is None:
        raise NotFoundError("Rule not found", details={"id": rule_id})

    # Build a copy name that doesn't collide. The naive "{name} (copy)"
    # collides when duplicating twice; cycle through " (copy 2)", "...3"
    # until we find a free slot. Bounded loop because Rule.name is
    # CHAR(120); we cap at 99 attempts to avoid a runaway in pathological
    # cases (operator already has "Foo (copy)" through "Foo (copy 98)").
    base = rule.name
    candidate = f"{base} (copy)"
    n = 2
    while await repo.get_by_name(candidate):
        candidate = f"{base} (copy {n})"
        n += 1
        if n > 100:
            # Bail with a timestamp suffix rather than loop forever.
            candidate = f"{base} (copy {_dt_now_short()})"
            break

    copy = Rule(
        name=candidate,
        description=rule.description,
        # Copies start disabled — see docstring.
        enabled=False,
        priority=rule.priority,
        definition=rule.definition,
        # Stage 29: duplication always produces a writable custom
        # rule, even when duplicating a builtin. That's the
        # "duplicate as custom rule" UX — the operator wanted a
        # divergent version, and divergence requires editability.
        is_builtin=False,
    )
    await repo.add(copy)
    await session.commit()
    return RuleRead.model_validate(copy)


@router.get(
    "/bundle/export",
    response_model=RuleExportBundle,
    summary="Export every rule as a portable bundle",
)
async def export_rules(
    _user: CurrentUser,
    session: SessionDep,
    include_builtins: bool = False,
) -> RuleExportBundle:
    """Returns every rule's definition in a content-addressable
    bundle. Volatile state (id, timestamps, evaluation counters) is
    excluded so two instances importing the same bundle land identical
    rules. Non-admin readable — exports are typically used to seed a
    second instance or to back up the configuration, neither of which
    requires admin gating to perform.

    Stage 29: built-in rules are excluded from the export by
    default. Every Auditarr installation has the same builtins
    seeded at startup, so re-importing them would just generate
    collision noise. Pass ``include_builtins=true`` to include them
    anyway (useful for diffing one installation's customized state
    against the codebase defaults).
    """
    rules = await RuleRepository(session).list_all()
    if not include_builtins:
        rules = [r for r in rules if not r.is_builtin]
    entries = [
        RuleExportEntry(
            name=r.name,
            description=r.description,
            enabled=r.enabled,
            priority=r.priority,
            definition=r.definition,
        )
        for r in rules
    ]
    return RuleExportBundle(
        version="1",
        exported_at=_dt_utcnow(),
        rules=entries,
    )


@router.post(
    "/bundle/import",
    response_model=RuleImportResponse,
    summary="Import a rule bundle with conflict resolution",
)
async def import_rules(
    body: RuleImportRequest,
    _admin: AdminUser,
    session: SessionDep,
) -> RuleImportResponse:
    """Import rules from an :class:`RuleExportBundle`. Each entry is
    validated against the rule schema; entries with invalid
    definitions are reported in the outcomes list with
    ``action="error"`` rather than blowing up the whole import — the
    operator can fix the offenders in the source bundle and re-run.

    Bundle version is checked against the supported set. We accept
    only ``version="1"`` today; future bumps may broaden this.
    """
    if body.bundle.version != "1":
        raise ValidationError(
            f"Unsupported bundle version {body.bundle.version!r}; "
            "this server reads version '1'",
        )

    repo = RuleRepository(session)
    outcomes: list[RuleImportOutcome] = []
    created = skipped = renamed = overwritten = errors = 0

    # Track names we mint during this import so a single bundle that
    # repeats a name doesn't collide with itself.
    minted_names: set[str] = set()

    for entry in body.bundle.rules:
        # Validate the definition up front. A bundle that mixes good
        # and bad entries should still import the good ones.
        try:
            _validate_definition(entry.definition)
        except ValidationError as exc:
            outcomes.append(
                RuleImportOutcome(
                    name=entry.name,
                    final_name=entry.name,
                    action="error",
                    error=exc.message,
                )
            )
            errors += 1
            continue

        existing = await repo.get_by_name(entry.name)
        if existing is None and entry.name not in minted_names:
            # No collision — straight create.
            rule = Rule(
                name=entry.name,
                description=entry.description,
                enabled=entry.enabled,
                priority=entry.priority,
                definition=entry.definition,
            )
            await repo.add(rule)
            minted_names.add(entry.name)
            outcomes.append(
                RuleImportOutcome(
                    name=entry.name,
                    final_name=entry.name,
                    action="created",
                    rule_id=rule.id,
                )
            )
            created += 1
            continue

        # Collision. Strategy decides what happens next.
        if body.on_conflict == "skip":
            outcomes.append(
                RuleImportOutcome(
                    name=entry.name,
                    final_name=entry.name,
                    action="skipped",
                    rule_id=existing.id if existing else None,
                )
            )
            skipped += 1
        elif body.on_conflict == "overwrite":
            # Existing must be in the DB (not just freshly-minted in
            # this batch) to actually overwrite; otherwise we'd be
            # mutating the in-memory rule we just created and the
            # operator's mental model would break.
            if existing is None:
                # Minted-this-batch collision under overwrite → treat
                # as rename to avoid clobbering the prior entry.
                resolved = await _next_available_name(
                    entry.name, minted_names, repo
                )
                rule = Rule(
                    name=resolved,
                    description=entry.description,
                    enabled=entry.enabled,
                    priority=entry.priority,
                    definition=entry.definition,
                )
                await repo.add(rule)
                minted_names.add(resolved)
                outcomes.append(
                    RuleImportOutcome(
                        name=entry.name,
                        final_name=resolved,
                        action="renamed",
                        rule_id=rule.id,
                    )
                )
                renamed += 1
            else:
                # Stage 29: never overwrite a builtin via import.
                # The codebase owns the canonical definition; an
                # operator-supplied overwrite would be transient
                # (next startup re-seeds). Surface as a skip with
                # a clear action so the import report tells the
                # operator what happened.
                if existing.is_builtin:
                    outcomes.append(
                        RuleImportOutcome(
                            name=entry.name,
                            final_name=entry.name,
                            action="skipped",
                            rule_id=existing.id,
                            error=(
                                "Cannot overwrite a built-in rule. "
                                "Duplicate the builtin first if you need a custom variant."
                            ),
                        )
                    )
                    skipped += 1
                    continue
                existing.description = entry.description
                existing.enabled = entry.enabled
                existing.priority = entry.priority
                existing.definition = entry.definition
                outcomes.append(
                    RuleImportOutcome(
                        name=entry.name,
                        final_name=entry.name,
                        action="overwritten",
                        rule_id=existing.id,
                    )
                )
                overwritten += 1
        else:  # rename
            resolved = await _next_available_name(
                entry.name, minted_names, repo
            )
            rule = Rule(
                name=resolved,
                description=entry.description,
                enabled=entry.enabled,
                priority=entry.priority,
                definition=entry.definition,
            )
            await repo.add(rule)
            minted_names.add(resolved)
            outcomes.append(
                RuleImportOutcome(
                    name=entry.name,
                    final_name=resolved,
                    action="renamed",
                    rule_id=rule.id,
                )
            )
            renamed += 1

    await session.commit()
    return RuleImportResponse(
        created=created,
        skipped=skipped,
        renamed=renamed,
        overwritten=overwritten,
        errors=errors,
        outcomes=outcomes,
    )


async def _next_available_name(
    base: str, minted: set[str], repo: RuleRepository
) -> str:
    """Find the next ``base (n)`` not used in the DB and not minted in
    the current import batch. Mirrors the duplicate-rule loop with the
    same 100-attempt cap + timestamp bail."""
    n = 2
    candidate = f"{base} (imported)"
    if candidate not in minted and await repo.get_by_name(candidate) is None:
        return candidate
    while True:
        candidate = f"{base} (imported {n})"
        if candidate not in minted and await repo.get_by_name(candidate) is None:
            return candidate
        n += 1
        if n > 100:
            return f"{base} (imported {_dt_now_short()})"


# ── v1.9 Stage 9.3 — AI suggestion generator ────────────────────


class AIGenerateRequest(BaseModel):
    """Optional body for the AI suggestion endpoint. When
    ``provider_integration_id`` is unset, the service picks the
    first enabled ``ai_provider`` Integration."""

    provider_integration_id: str | None = None


@router.post(
    "/suggestions/ai-generate",
    summary="Ask the configured AI provider to propose new rules",
)
async def ai_generate_suggestions(
    _admin: AdminUser,
    session: SessionDep,
    body: AIGenerateRequest | None = None,
) -> dict[str, object]:
    """v1.9 Stage 9.3 — admin-only endpoint that triggers the AI
    suggestion generator.

    Returns a structured outcome:
      * ``suggestions_created`` — int, count of new RuleSuggestion
        rows persisted.
      * ``candidates_received`` — int, raw proposals from the AI
        before validation.
      * ``candidates_rejected`` — int, proposals that failed
        ``RuleDefinition.model_validate``.
      * ``budget_exceeded`` — bool, True when the integration's
        ``daily_call_budget`` was already used up. In that case
        no HTTP call was made.
      * ``provider_kind`` / ``provider_integration_id`` — which
        provider was used.
      * ``error`` — string when the provider call failed or no
        provider integration is configured.

    The endpoint surfaces 200 even on provider-level errors —
    the operator's UI banner reads the ``error`` field. A 5xx
    would be misleading: the request reached our service
    correctly; only the AI hand-off failed.
    """
    from app.services.ai.suggestions import AISuggestionService

    service = AISuggestionService(session=session)
    result = await service.generate(
        provider_integration_id=(
            body.provider_integration_id if body else None
        ),
    )
    return {
        "suggestions_created": result.suggestions_created,
        "candidates_received": result.candidates_received,
        "candidates_rejected": result.candidates_rejected,
        "budget_exceeded": result.budget_exceeded,
        "provider_kind": result.provider_kind,
        "provider_integration_id": result.provider_integration_id,
        "error": result.error,
    }
