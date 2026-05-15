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
    Quarantine,
    QueueOptimization,
    RuleDefinition,
    SetSeverity,
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


@dataclass(slots=True)
class EvaluationResult:
    """What the evaluator decided for one (rule, file) pair."""

    matched: bool
    severity: str | None = None  # set by ``set_severity`` actions
    severity_rank: int = 0
    add_tags: list[str] = field(default_factory=list)
    queue_optimizations: list[str] = field(default_factory=list)
    notifications: list[dict[str, str | None]] = field(default_factory=list)
    # Stage 9 (audit follow-up): quarantine + delete decisions.
    # ``quarantine`` is True when at least one matched action is a
    # Quarantine action OR a Delete action (soft-delete falls back
    # to quarantine when ``confirm=False``). ``quarantine_reason``
    # is the first non-null reason supplied across the matched
    # quarantine actions.
    quarantine: bool = False
    quarantine_reason: str | None = None
    # ``delete_paths`` is populated with the file's path when a
    # Delete(confirm=True) action matched. The service layer
    # consumes this list; the evaluator never touches the
    # filesystem.
    delete_paths: list[str] = field(default_factory=list)

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
        # Stage 9: quarantine is a one-way escalation — once any rule
        # quarantines, the aggregate stays quarantined. Reason carries
        # the first non-null value (preserves the most specific
        # message rather than overwriting it).
        if self.quarantine and not other.quarantine:
            other.quarantine = True
            other.quarantine_reason = self.quarantine_reason
        elif self.quarantine_reason and not other.quarantine_reason:
            other.quarantine_reason = self.quarantine_reason
        # delete_paths accumulates so the service can dedupe.
        other.delete_paths.extend(self.delete_paths)


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
        result.notifications.append(
            {"channel": action.channel, "message": action.message}
        )
        return
    if isinstance(action, Quarantine):
        # Stage 9 (audit follow-up): quarantine is a flag the service
        # layer reads; the evaluator stays pure (no DB writes here).
        result.quarantine = True
        if action.reason and not result.quarantine_reason:
            result.quarantine_reason = action.reason
        return
    if isinstance(action, Delete):
        # Stage 9 (audit follow-up): Defensive default. Without
        # ``confirm=True`` the action soft-deletes (quarantine +
        # flag); with confirm, we surface the path for the service
        # to move to trash + delete the row.
        result.quarantine = True
        if not result.quarantine_reason:
            result.quarantine_reason = (
                "Soft-delete via rule (confirm=false)"
                if not action.confirm
                else "Pending hard delete via rule"
            )
        if action.confirm:
            # Stage 9 (audit follow-up): the service layer reads
            # ``result.delete_paths`` to drive the actual filesystem
            # move + row removal. The evaluator stays pure (no I/O);
            # it just records the path the matched rule wants deleted.
            result.delete_paths.append(input_.path)
        return
    raise TypeError(f"Unknown action type: {type(action)!r}")
