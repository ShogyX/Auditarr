"""Rule DSL — schema validation.

A rule document looks like::

    {
      "match": {
        "all": [
          {"field": "video_codec", "op": "in", "value": ["hevc", "x265"]},
          {"field": "bitrate_kbps", "op": "gt", "value": 25000}
        ]
      },
      "actions": [
        {"type": "set_severity", "severity": "warn"},
        {"type": "add_tag", "tag": "high-bitrate-hevc"}
      ]
    }

Conditions support combining via ``all`` / ``any``. Conditions are leaves
referencing a single field, with one of a fixed set of operators.

The grammar is intentionally small and frozen: the evaluator is pure, the
DSL is the user-facing surface for years to come, and "let me add another
operator later" is much safer than "let me take one away".
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Severity scale (must align with MediaFile.severity_rank) ────────
SEVERITY_LEVELS: dict[str, int] = {
    "ok": 10,
    "info": 20,
    "warn": 40,
    "high": 60,
    "error": 80,
    "crit": 100,
}
"""Map of severity label → numeric rank. Higher = worse.

The evaluator uses ``rank`` to enforce monotonic escalation: a rule can
only raise a file's severity, never lower it. This makes the engine
order-independent — every file ends up at the maximum severity any
matching rule decided to apply.
"""


# ── Conditions ────────────────────────────────────────────────────────
# Fields a condition can reference. The evaluator resolves these against
# a :class:`EvaluationInput` (a MediaFile + its tags). New fields are an
# additive change.
SUPPORTED_FIELDS: frozenset[str] = frozenset(
    {
        "filename",
        "extension",
        "category",
        "container",
        "video_codec",
        "audio_codec",
        "subtitle_codec",
        "width",
        "height",
        "duration_seconds",
        "bitrate_kbps",
        "framerate",
        "size_bytes",
        "has_subtitles",
        "is_orphaned",
        "subtitle_languages",
        "audio_languages",
        "tags",
    }
)

# Numeric ops apply to numeric fields, string ops to strings, set ops to
# array-shaped fields like ``tags``. Validation in :class:`Condition`
# enforces compatibility.
NUMERIC_OPS: frozenset[str] = frozenset({"eq", "ne", "lt", "lte", "gt", "gte"})
STRING_OPS: frozenset[str] = frozenset({"eq", "ne", "in", "regex"})
SET_OPS: frozenset[str] = frozenset({"contains", "not_contains", "any_of", "none_of"})
BOOL_OPS: frozenset[str] = frozenset({"eq", "ne"})

NUMERIC_FIELDS: frozenset[str] = frozenset(
    {"width", "height", "duration_seconds", "bitrate_kbps", "framerate", "size_bytes"}
)
BOOL_FIELDS: frozenset[str] = frozenset({"has_subtitles", "is_orphaned"})
ARRAY_FIELDS: frozenset[str] = frozenset(
    {"subtitle_languages", "audio_languages", "tags"}
)
# Everything else in SUPPORTED_FIELDS is treated as string.


class Condition(BaseModel):
    """A leaf condition: `field op value`."""

    model_config = ConfigDict(extra="forbid")

    field: str
    op: str
    value: Any

    @field_validator("field")
    @classmethod
    def _validate_field(cls, v: str) -> str:
        if v not in SUPPORTED_FIELDS:
            raise ValueError(
                f"Unsupported field {v!r}. Supported: {sorted(SUPPORTED_FIELDS)}"
            )
        return v

    @field_validator("op")
    @classmethod
    def _validate_op(cls, v: str, info) -> str:  # type: ignore[no-untyped-def]
        field = info.data.get("field")
        if field is None:
            return v
        allowed = _ops_for_field(field)
        if v not in allowed:
            raise ValueError(
                f"Operator {v!r} not valid for field {field!r}. "
                f"Allowed: {sorted(allowed)}"
            )
        return v


def _ops_for_field(field: str) -> frozenset[str]:
    if field in NUMERIC_FIELDS:
        return NUMERIC_OPS
    if field in BOOL_FIELDS:
        return BOOL_OPS
    if field in ARRAY_FIELDS:
        # Array fields support set ops AND ``eq``-ish length comparisons via
        # ``contains``/``any_of``. Plain ``eq`` would be confusing on a list
        # so it's deliberately omitted.
        return SET_OPS
    return STRING_OPS


# ── Combinators ───────────────────────────────────────────────────────
class AllOf(BaseModel):
    model_config = ConfigDict(extra="forbid")
    all_: list["Match"] = Field(alias="all", min_length=1)


class AnyOf(BaseModel):
    model_config = ConfigDict(extra="forbid")
    any_: list["Match"] = Field(alias="any", min_length=1)


Match = Annotated[
    Union[AllOf, AnyOf, Condition],
    Field(description="A combinator (all/any) or a leaf condition."),
]


# ── Actions ───────────────────────────────────────────────────────────
class SetSeverity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["set_severity"]
    severity: str

    @field_validator("severity")
    @classmethod
    def _check(cls, v: str) -> str:
        if v not in SEVERITY_LEVELS:
            raise ValueError(
                f"Unknown severity {v!r}. Allowed: {list(SEVERITY_LEVELS)}"
            )
        return v


class AddTag(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["add_tag"]
    tag: str = Field(min_length=1, max_length=64)


class QueueOptimization(BaseModel):
    """Hand-off to the optimization pipeline (Stage 10)."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["queue_optimization"]
    profile: str = Field(min_length=1, max_length=64)


class Notify(BaseModel):
    """Hand-off to the notification engine (Stage 9)."""

    model_config = ConfigDict(extra="forbid")
    type: Literal["notify"]
    channel: str = Field(min_length=1, max_length=64)
    message: str | None = Field(default=None, max_length=512)


class Quarantine(BaseModel):
    """Quarantine the matched file (Stage 9 audit follow-up).

    Sets ``MediaFile.quarantined=True`` and emits ``media.quarantined``.
    The reason is optional and persists on the row for the operator
    to see. Pre-Stage-9, quarantining was only reachable via the
    manual endpoint or the orphan-cleanup path; this lets rules
    automate it.
    """

    model_config = ConfigDict(extra="forbid")
    type: Literal["quarantine"]
    reason: str | None = Field(default=None, max_length=256)


class Delete(BaseModel):
    """Delete the matched file (Stage 9 audit follow-up).

    Defensive default: ``confirm=False`` resolves to a soft-delete
    (quarantine + mark for deletion) so a misconfigured rule can't
    nuke a library overnight. Only with ``confirm=True`` does the
    service actually move the file to ``data_dir/trash/`` and
    remove the row.
    """

    model_config = ConfigDict(extra="forbid")
    type: Literal["delete"]
    confirm: bool = Field(
        default=False,
        description=(
            "Hard delete switch. False (default) = soft-delete "
            "(quarantine + flag); True = move the file to "
            "data_dir/trash/ and remove the MediaFile row."
        ),
    )


Action = Annotated[
    Union[SetSeverity, AddTag, QueueOptimization, Notify, Quarantine, Delete],
    Field(discriminator="type"),
]


# ── Top-level rule ────────────────────────────────────────────────────
class RuleDefinition(BaseModel):
    """The body of a rule, validated on save and on load."""

    model_config = ConfigDict(extra="forbid")

    match: Match
    actions: list[Action] = Field(min_length=1)


# Forward refs for self-referencing combinators.
AllOf.model_rebuild()
AnyOf.model_rebuild()
