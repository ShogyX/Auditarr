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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
        # Stage 06 (v1.7) — rule engine extensions.
        # ``probe_failed`` (bool): True when ffprobe couldn't read
        # the file. Populated by the scanner; reset to False on a
        # successful re-probe. Enables the built-in "Probe failed"
        # rule to fire only on rows where the probe actually failed
        # (pre-Stage-06 the rule was stubbed because there was no
        # column to match on).
        "probe_failed",
        # ``vt_status`` (string, literal): VirusTotal scan result.
        # Stored as a column on MediaFile (real column, not a
        # computed property — simpler queries, easier indexing).
        # Allowed values are ``VT_STATUS_VALUES``. Stage 10 will
        # wire the VT plugin to populate this column; Stage 06
        # adds the field so the built-in "VirusTotal non-clean"
        # rule validates and is available for operator use.
        "vt_status",
    }
)

# Stage 06 (v1.7) — allowed values for the ``vt_status`` field.
# Defined here so both the rule engine and the VT plugin (Stage
# 10) reference the same canonical list. A rule body that uses
# any other value is rejected at validation; the VT plugin
# writes only these strings into the column.
VT_STATUS_VALUES: frozenset[str] = frozenset(
    {"clean", "malicious", "suspicious", "not_found", "error"}
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
BOOL_FIELDS: frozenset[str] = frozenset(
    {"has_subtitles", "is_orphaned", "probe_failed"}
)
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

    @field_validator("value")
    @classmethod
    def _validate_value(cls, v: Any, info) -> Any:  # type: ignore[no-untyped-def]
        """Stage 06 (v1.7): enforce literal values where the DSL
        ships a fixed enumeration.

        ``vt_status`` is the first such field — values must be in
        ``VT_STATUS_VALUES`` (clean/malicious/suspicious/not_found/
        error). For the ``in`` op the value is a list; we check
        each element. Other fields pass through (the per-op
        type checks live in the evaluator at runtime; the schema
        intentionally keeps ``value: Any`` so callers can author
        rules without contorting types through JSON)."""
        field = info.data.get("field")
        op = info.data.get("op")
        if field == "vt_status":
            if op == "in":
                if not isinstance(v, list) or not v:
                    raise ValueError(
                        "vt_status with op 'in' requires a non-empty list"
                    )
                bad = [x for x in v if x not in VT_STATUS_VALUES]
                if bad:
                    raise ValueError(
                        f"vt_status value(s) {bad!r} not in allowed set "
                        f"{sorted(VT_STATUS_VALUES)}"
                    )
            else:
                if v not in VT_STATUS_VALUES:
                    raise ValueError(
                        f"vt_status value {v!r} not in allowed set "
                        f"{sorted(VT_STATUS_VALUES)}"
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


class NotifyThrottle(BaseModel):
    """Stage 06 (v1.7) — notification throttle window.

    A rule with ``Notify(throttle=...)`` will deliver at most
    ``max_per_window`` notifications inside any rolling
    ``window_seconds``-second window. Beyond that limit, the
    rules engine emits a ``rule.throttled`` event (consumed by
    the dashboard) and writes one summary audit-log entry per
    window per rule — NOT one per suppressed event (per addendum
    A.2, §125: "every throttle-suppressed notification → one
    summary entry per window per rule (not per suppressed
    event)").

    Default unthrottled: the field is optional on ``Notify``.
    ``window_seconds`` minimum 60 — anything shorter is more
    likely a typo than a deliberate sub-minute throttle.
    ``max_per_window`` minimum 1 — a value of 0 would mean
    "never send", which the operator should express by
    disabling the rule instead.
    """

    model_config = ConfigDict(extra="forbid")
    window_seconds: int = Field(ge=60)
    max_per_window: int = Field(ge=1)


class Notify(BaseModel):
    """Hand-off to the notification engine (Stage 9), extended in
    Stage 06 (v1.7) with optional throttle.

    The ``message`` field already shipped in Stage 9 — Stage 06
    confirms it flows through ``notifications/templating.py`` to
    the rendered email body. When the same rule also includes a
    ``delete`` action, the email template renders an extra
    "auto-deleting; no action required" badge so the operator
    knows the file has already been removed by the time the
    email arrives.
    """

    model_config = ConfigDict(extra="forbid")
    type: Literal["notify"]
    channel: str = Field(min_length=1, max_length=64)
    message: str | None = Field(default=None, max_length=512)
    throttle: NotifyThrottle | None = Field(default=None)


class Delete(BaseModel):
    """Delete the matched file (Stage 05 v1.7 — "delete means delete").

    Stage 05 retired the quarantine intermediate state (Section
    A.0 of the v1.7 addendum). A delete action now unconditionally
    moves the file to ``data_dir/trash/`` and removes the
    ``MediaFile`` row. The ``trash`` subdirectory remains the
    operator's recovery surface — a misconfigured rule is still
    recoverable by moving files back out of trash.

    The pre-Stage-05 ``confirm`` flag is gone — the soft-delete
    branch it gated (quarantine + flag) no longer exists. Stored
    rule bodies that still carry ``confirm`` are migrated by the
    0015 migration (the flag is dropped silently — every persisted
    Delete already had ``confirm`` as a no-op gate on the new
    semantics).

    ``reason`` is preserved for the audit log entry that every
    successful delete emits via ``AuditService``. Operators
    reading the audit trail see WHY a file was removed, not just
    WHEN. The field is optional; when absent, the service
    synthesizes a generic "Deleted by rule" reason so the audit
    row still carries something readable.
    """

    model_config = ConfigDict(extra="forbid")
    type: Literal["delete"]
    reason: str | None = Field(
        default=None,
        max_length=256,
        description=(
            "Human-readable reason recorded in the audit log "
            "entry for the delete. Optional but recommended — "
            "the audit trail surfaces this verbatim."
        ),
    )


# Stage 05 (v1.7) — the ``Quarantine`` action class is removed.
# Stored rule bodies that referenced ``type: "quarantine"`` are
# rewritten to ``type: "delete"`` during the 0015 migration; new
# rule submissions that include ``type: "quarantine"`` fail
# validation because that literal is no longer in the
# discriminated union below.


# v1.9 Stage 4.6 — VT lookup as a rule action.
class VtLookup(BaseModel):
    """Enqueue the matched file for a VirusTotal lookup.

    The action is a write to the VT queue, not a direct API call —
    the VT plugin's worker drains the queue at its own quota-
    respecting cadence. Operators use this for scoped lookups
    that the scanner's automatic enqueue can't express: "lookup
    only files matching THIS rule" (e.g. extension == 'exe',
    extension == 'iso', tags contains 'downloaded').

    The action has no parameters today — the VT plugin is the
    source of truth for which API tier / quota window / wait
    strategy to use. We keep the schema open for future params
    (priority, scan-now-vs-deferred) by using ``extra="forbid"``
    so a typo in a future param fails loudly rather than
    silently doing nothing.

    Distinct from the scanner-side ``enqueue_for_vt_lookup`` path
    (which the Stage 4.6 scope-restriction config governs); both
    paths terminate in the same ``vt_queue`` table.
    """

    model_config = ConfigDict(extra="forbid")
    type: Literal["vt_lookup"]


# v1.9 Stage 5.1 — Cross-integration search trigger.
class SearchUpstream(BaseModel):
    """Trigger a search on an upstream integration (Sonarr / Radarr /
    Bazarr) for the matched file.

    Use cases:
      * "When a file becomes orphaned, trigger Sonarr to re-search
        the series."
      * "When a 4K HEVC file gets flagged crit, kick Bazarr to
        search for English subtitles."

    The rule engine flags the request on the EvaluationResult; the
    service layer enqueues a worker job (one per unique
    (integration_id, media_file_id) pair, deduplicated). The worker
    calls the provider's ``trigger_search`` method, which resolves
    the upstream id and submits the search.

    Schema:
      * ``target`` is the kind discriminator ("sonarr" / "radarr" /
        "bazarr"). The discriminator is validated against the
        SEARCH_UPSTREAM_TARGETS set below.
      * ``integration_id`` is the operator-selected integration row.
        Required so the operator can have multiple Sonarr
        integrations and pick which one to fire.
    """

    model_config = ConfigDict(extra="forbid")
    type: Literal["search_upstream"]
    target: str = Field(
        ...,
        description=(
            "Upstream integration kind: 'sonarr', 'radarr', or 'bazarr'."
        ),
    )
    integration_id: str = Field(
        ...,
        min_length=1,
        description="ID of the enabled integration to call.",
    )

    @field_validator("target")
    @classmethod
    def _validate_target(cls, v: str) -> str:
        v_norm = v.strip().lower()
        if v_norm not in SEARCH_UPSTREAM_TARGETS:
            raise ValueError(
                f"target must be one of {sorted(SEARCH_UPSTREAM_TARGETS)}, "
                f"got {v!r}"
            )
        return v_norm


SEARCH_UPSTREAM_TARGETS: frozenset[str] = frozenset({"sonarr", "radarr", "bazarr"})


Action = Annotated[
    Union[
        SetSeverity,
        AddTag,
        QueueOptimization,
        Notify,
        Delete,
        VtLookup,
        SearchUpstream,
    ],
    Field(discriminator="type"),
]


# ── Top-level rule ────────────────────────────────────────────────────
class RuleDefinition(BaseModel):
    """The body of a rule, validated on save and on load.

    Stage 06 (v1.7) — destructive-action acknowledgement.

    Per addendum A.0.1: a rule that contains any ``delete`` action
    MUST carry an ``acknowledged_destructive: true`` flag at the
    rule level. This is the defensive layer that replaced the
    pre-Stage-05 ``Delete.confirm`` flag — the operator
    acknowledges, once per rule, that this rule will remove
    files. Without the acknowledgement, the API refuses to save.

    The acknowledgement is at the rule level (not the action
    level) deliberately: a rule may have multiple delete
    actions through aggregation, and the operator's intent is
    "I understand this rule deletes files", not "I confirm each
    individual delete action". The visual rule builder surfaces
    this as a single checkbox labeled "I understand this rule
    deletes files from disk."

    Rules without any delete action MUST NOT carry
    ``acknowledged_destructive: true`` — that would be
    misleading. The validator rejects bodies that set the flag
    without a corresponding delete action.
    """

    model_config = ConfigDict(extra="forbid")

    match: Match
    actions: list[Action] = Field(min_length=1)
    acknowledged_destructive: bool = Field(
        default=False,
        description=(
            "Operator acknowledges this rule will delete files "
            "from disk. Required when ``actions`` contains any "
            "``delete`` action; forbidden otherwise."
        ),
    )

    @model_validator(mode="after")
    def _validate_destructive_ack(self) -> "RuleDefinition":
        """Stage 06 (v1.7) — destructive-action acknowledgement.

        ``model_validator(mode="after")`` runs after all fields are
        populated (including defaults), so the check fires even
        when the operator omits ``acknowledged_destructive`` from
        the request body — that's the common case we care about
        rejecting."""
        has_delete = any(
            getattr(a, "type", None) == "delete" for a in self.actions
        )
        if has_delete and not self.acknowledged_destructive:
            raise ValueError(
                "Rules containing a 'delete' action require "
                "acknowledged_destructive: true at the rule level. "
                "This is the Stage 06 v1.7 defensive layer that "
                "replaced the retired Delete.confirm flag."
            )
        if self.acknowledged_destructive and not has_delete:
            raise ValueError(
                "acknowledged_destructive: true is forbidden on "
                "rules without a 'delete' action; set it only on "
                "rules that actually delete files."
            )
        return self


# Forward refs for self-referencing combinators.
AllOf.model_rebuild()
AnyOf.model_rebuild()
