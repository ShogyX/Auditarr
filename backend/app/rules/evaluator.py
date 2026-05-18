"""Rule evaluator.

The evaluator is a pure function: it takes the structured data describing
a media file plus its tags and the parsed rule definition, and returns
the actions the rule would apply if the conditions match. It never reads
or writes the database — the service layer wraps this.

Keeping the evaluator pure has three benefits:

1. **Trivially testable.** No fixtures, no async, no I/O.
2. **Reusable in dry-run mode.** The UI's "show me what this rule would
   match" feature calls this directly against fetched rows.
3. **Cheap to call.** The scanner runs this for every file on every scan;
   any hidden I/O would be ruinous.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.rules.schema import (
    SEVERITY_LEVELS,
    Action,
    AddTag,
    AllOf,
    AnyOf,
    Condition,
    Delete,
    Match,
    Notify,
    QueueOptimization,
    RuleDefinition,
    SearchUpstream,
    SetSeverity,
    VtLookup,
)


@dataclass(slots=True)
class EvaluationInput:
    """Everything the evaluator needs to evaluate a rule against one file.

    Fed by the service layer, which materializes the row + its tags. Using
    a dataclass means tests don't need a real SQLAlchemy object.
    """

    # ── Identity ──
    media_file_id: str
    path: str
    filename: str
    extension: str
    category: str
    # ── Tech metadata (may be None for non-media files) ──
    container: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    subtitle_codec: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    bitrate_kbps: int | None = None
    framerate: float | None = None
    size_bytes: int = 0
    has_subtitles: bool = False
    is_orphaned: bool = False
    subtitle_languages: list[str] = field(default_factory=list)
    audio_languages: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    # Stage 06 (v1.7) — rule engine extensions.
    #
    # ``probe_failed``: True when ffprobe couldn't read the file
    # (set by ``scanner.py``; cleared on a successful re-probe).
    # Powers the Stage 06 built-in "Probe failed" rule which
    # used to be stubbed because the column didn't exist as a
    # DSL field.
    probe_failed: bool = False
    # ``vt_status``: VirusTotal scan result; one of
    # ``VT_STATUS_VALUES`` (clean/malicious/suspicious/not_found/
    # error) or ``None`` for "never looked up". Populated by the
    # VT plugin (Stage 10). Stage 06 wires the DSL field so the
    # built-in "VirusTotal non-clean" rule has somewhere to
    # match — even before Stage 10, operator-authored rules can
    # use this field and will simply not match until a VT result
    # arrives.
    vt_status: str | None = None


@dataclass(slots=True)
class EvaluationResult:
    """What the evaluator decided for one (rule, file) pair."""

    matched: bool
    severity: str | None = None  # set by ``set_severity`` actions
    severity_rank: int = 0
    add_tags: list[str] = field(default_factory=list)
    queue_optimizations: list[str] = field(default_factory=list)
    notifications: list[dict[str, Any]] = field(default_factory=list)
    # Stage 05 (v1.7): delete decisions. The quarantine intermediate
    # state retired in this stage — "delete means delete" (Section
    # A.0 of the v1.7 addendum). Per matched ``Delete`` action, the
    # evaluator records the file's path and the operator-supplied
    # reason (or a synthesized one when no reason was given). The
    # service layer reads ``delete_paths`` to move the file to
    # trash + drop the row, and uses ``delete_reasons`` for the
    # audit-log entry it emits for every successful delete.
    #
    # Two parallel lists keep the (path, reason) pairing stable
    # without forcing a TypedDict on every downstream consumer.
    # They're always the same length; ``merge_into`` extends both
    # in lockstep.
    delete_paths: list[str] = field(default_factory=list)
    delete_reasons: list[str] = field(default_factory=list)
    # v1.9 Stage 4.6 — VT lookup as a rule action. When a rule's
    # ``vt_lookup`` action matches, the evaluator flips this to
    # True; the service layer reads the flag and enqueues the
    # file into the VT queue (same write target as the scanner's
    # auto-enqueue path). A boolean is enough — a file matched by
    # multiple ``vt_lookup`` actions still results in one queue
    # entry; the VT queue is idempotent on (media_file_id).
    vt_lookup_requested: bool = False
    # v1.9 Stage 5.1 — Cross-integration search trigger. Each
    # matched ``search_upstream`` action appends one entry of
    # ``{"target": str, "integration_id": str}``. The service
    # layer reads the list, deduplicates by (integration_id,
    # media_file_id), and enqueues one worker job per unique
    # pair. Two parallel ``search_upstream`` actions on the same
    # rule (e.g. one for Sonarr and one for Bazarr) yield two
    # entries — distinct integrations, distinct jobs.
    search_upstream_requests: list[dict[str, str]] = field(default_factory=list)

    def merge_into(self, other: "EvaluationResult") -> None:
        """Combine this result's escalations into ``other``.

        Used by the service layer when reducing many per-rule results down
        to a single aggregate decision for a file. Tags accumulate (deduped);
        severity escalates monotonically.
        """
        if self.severity is not None and self.severity_rank > other.severity_rank:
            other.severity = self.severity
            other.severity_rank = self.severity_rank
        seen = set(other.add_tags)
        for tag in self.add_tags:
            if tag not in seen:
                other.add_tags.append(tag)
                seen.add(tag)
        other.queue_optimizations.extend(self.queue_optimizations)
        other.notifications.extend(self.notifications)
        # Stage 05: delete_paths + delete_reasons stay paired by index.
        other.delete_paths.extend(self.delete_paths)
        other.delete_reasons.extend(self.delete_reasons)
        # v1.9 Stage 4.6 — boolean OR (any matching rule's
        # vt_lookup action escalates the aggregate).
        if self.vt_lookup_requested:
            other.vt_lookup_requested = True
        # v1.9 Stage 5.1 — accumulate search-upstream requests.
        # The service layer is responsible for deduplication; we
        # extend the raw list here so the merge stays pure
        # concatenation (no per-merge dedup cost, which would
        # otherwise be O(N²) across many rules).
        other.search_upstream_requests.extend(self.search_upstream_requests)


# ── Public API ────────────────────────────────────────────────────────
def evaluate(
    definition: RuleDefinition, input_: EvaluationInput
) -> EvaluationResult:
    """Apply ``definition`` to ``input_`` and return the proposed actions."""
    if not _match(definition.match, input_):
        return EvaluationResult(matched=False)

    result = EvaluationResult(matched=True)
    for action in definition.actions:
        _apply_action(action, result, input_)
    return result


# ── Match evaluation ──────────────────────────────────────────────────
def _match(node: Match, input_: EvaluationInput) -> bool:
    if isinstance(node, AllOf):
        return all(_match(child, input_) for child in node.all_)
    if isinstance(node, AnyOf):
        return any(_match(child, input_) for child in node.any_)
    if isinstance(node, Condition):
        return _eval_condition(node, input_)
    # Pydantic discriminator should make this unreachable.
    raise TypeError(f"Unknown match node: {type(node)!r}")


def _eval_condition(condition: Condition, input_: EvaluationInput) -> bool:
    value = getattr(input_, condition.field, None)
    # v1.9 Stage 4.1 — language-field comparisons normalize both
    # sides through ``normalize_language`` so a rule like
    # ``audio_languages contains "en"`` matches files whose
    # ffprobe-derived list contains ``eng`` / ``English`` /
    # ``en-US`` / etc. Pre-1.9 strict string equality made these
    # rules silently miss the cases operators most expected to
    # work. The two language fields are the only ones that get
    # this treatment — everything else (codecs, paths, tags)
    # stays case-sensitive and exact, matching pre-1.9 behavior.
    if condition.field in ("audio_languages", "subtitle_languages"):
        from app.rules.language_normalize import (
            normalize_language,
            normalize_languages,
        )

        actual_norm = normalize_languages(value if isinstance(value, list) else [])
        expected_raw = condition.value
        if isinstance(expected_raw, list):
            expected_norm = [
                n
                for n in (normalize_language(v) for v in expected_raw if isinstance(v, str))
                if n is not None
            ]
        elif isinstance(expected_raw, str):
            expected_norm = normalize_language(expected_raw)
        else:
            expected_norm = expected_raw
        return _apply_op(condition.op, actual_norm, expected_norm)
    return _apply_op(condition.op, value, condition.value)


def _apply_op(op: str, actual: Any, expected: Any) -> bool:
    # Numeric ops handle None as "doesn't match" rather than raising; a file
    # without bitrate metadata simply doesn't match "bitrate_kbps > X".
    if op == "eq":
        return actual == expected
    if op == "ne":
        return actual != expected
    if actual is None:
        # Remaining ops all require a value; bail safely.
        return False
    if op == "lt":
        return _as_number(actual) < _as_number(expected)
    if op == "lte":
        return _as_number(actual) <= _as_number(expected)
    if op == "gt":
        return _as_number(actual) > _as_number(expected)
    if op == "gte":
        return _as_number(actual) >= _as_number(expected)
    if op == "in":
        return actual in (expected or [])
    if op == "regex":
        if not isinstance(actual, str) or not isinstance(expected, str):
            return False
        try:
            return re.search(expected, actual) is not None
        except re.error:
            return False
    if op == "contains":
        return expected in (actual or [])
    if op == "not_contains":
        return expected not in (actual or [])
    if op == "any_of":
        items = expected if isinstance(expected, list) else [expected]
        return any(item in (actual or []) for item in items)
    if op == "none_of":
        items = expected if isinstance(expected, list) else [expected]
        return not any(item in (actual or []) for item in items)
    raise ValueError(f"Unknown op: {op!r}")


def _as_number(value: Any) -> float:
    if isinstance(value, bool):  # avoid True == 1 surprises
        return float(int(value))
    return float(value)


# ── Action application ────────────────────────────────────────────────
def _apply_action(
    action: Action,
    result: EvaluationResult,
    input_: EvaluationInput,
) -> None:
    if isinstance(action, SetSeverity):
        rank = SEVERITY_LEVELS[action.severity]
        # Monotonic escalation — only update if this action would raise.
        if rank > result.severity_rank:
            result.severity = action.severity
            result.severity_rank = rank
        return
    if isinstance(action, AddTag):
        if action.tag not in result.add_tags:
            result.add_tags.append(action.tag)
        return
    if isinstance(action, QueueOptimization):
        result.queue_optimizations.append(action.profile)
        return
    if isinstance(action, Notify):
        # Stage 06 (v1.7): surface the optional throttle config so
        # the service layer can decide whether to suppress this
        # notification before dispatch. The ``throttle`` value is
        # either a ``{"window_seconds": int, "max_per_window":
        # int}`` dict (when the operator configured one) or
        # ``None`` (unthrottled — every match delivers).
        notif: dict[str, Any] = {
            "channel": action.channel,
            "message": action.message,
            "throttle": action.throttle.model_dump()
            if action.throttle is not None
            else None,
        }
        result.notifications.append(notif)
        return
    if isinstance(action, Delete):
        # Stage 05 (v1.7) — "delete means delete" (Section A.0).
        # The pre-Stage-05 ``confirm`` flag is gone; every matched
        # Delete moves the file to ``data_dir/trash/`` and removes
        # the row. The reason supplied on the action flows through
        # to the audit-log entry the service layer emits; if no
        # reason was supplied, we synthesize a generic one so the
        # audit row still carries something operator-readable.
        result.delete_paths.append(input_.path)
        result.delete_reasons.append(action.reason or "Deleted by rule")
        return
    if isinstance(action, VtLookup):
        # v1.9 Stage 4.6 — flag the file for VT enqueue. The
        # service layer (RulesService) reads the flag and writes a
        # row to the vt_queue table; the VT plugin's worker drains
        # it at its own quota-respecting cadence. We don't pass
        # ``input_.media_file_id`` along here because the result
        # object is already keyed to one file (the service knows
        # which one).
        result.vt_lookup_requested = True
        return
    if isinstance(action, SearchUpstream):
        # v1.9 Stage 5.1 — record the request. Service layer
        # deduplicates and enqueues one worker job per unique
        # (integration_id, media_file_id). The action carries
        # both ``target`` (kind discriminator, redundant with the
        # integration row's kind but kept for explicit auditing)
        # and ``integration_id`` (which row to call).
        result.search_upstream_requests.append(
            {
                "target": action.target,
                "integration_id": action.integration_id,
            }
        )
        return
    raise TypeError(f"Unknown action type: {type(action)!r}")
