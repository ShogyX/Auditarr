"""Rules API schemas."""

from __future__ import annotations

import datetime as _dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RuleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=10_000)
    definition: dict[str, Any]


class RuleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=10_000)
    definition: dict[str, Any] | None = None


class RuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    enabled: bool
    priority: int
    definition: dict[str, Any]
    # Stage 29: True for rules seeded by the codebase. The Rules page
    # uses this to render a badge + read-only affordances and the API
    # uses it to gate mutations.
    is_builtin: bool = False
    last_evaluated_at: _dt.datetime | None
    last_match_count: int
    created_at: _dt.datetime
    updated_at: _dt.datetime


class RuleEvaluationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    media_file_id: str
    rule_id: str
    severity: str
    severity_rank: int
    actions_summary: dict[str, Any]
    evaluated_at: _dt.datetime


class RuleEvaluationFileRow(BaseModel):
    """Stage 14b (audit follow-up): one row for the per-rule
    "Matched files" tab.

    Joins ``rule_evaluations`` to ``media_files`` so the tab can
    render path / filename / severity without per-row fetches.
    Lives next to :class:`RuleEvaluationRead` because the two share
    the same source rows; the columns differ because the consumers
    differ (drawer wants rule names + actions, this tab wants
    filenames). The row is intentionally minimal — clicking it
    cross-links to the Files page where the full file detail lives.
    """

    model_config = ConfigDict(from_attributes=True)

    media_file_id: str
    library_id: str
    path: str
    filename: str
    severity: str
    severity_rank: int
    evaluated_at: _dt.datetime


class RuleDryRunRequest(BaseModel):
    """Try a candidate definition against an existing file without saving."""

    model_config = ConfigDict(extra="forbid")

    definition: dict[str, Any]
    media_file_id: str


class RuleDryRunResponse(BaseModel):
    matched: bool
    severity: str | None
    severity_rank: int
    add_tags: list[str]
    queue_optimizations: list[str]


class RuleEvaluateLibraryResponse(BaseModel):
    library_id: str
    files_evaluated: int


# ── Stage 15: rule vocabulary ────────────────────────────────
class RuleVocabularyField(BaseModel):
    """One condition-field the visual builder can offer."""

    model_config = ConfigDict(from_attributes=True)

    key: str  # raw field name (e.g. "video_codec")
    label: str  # display label ("Video codec")
    type: str  # "numeric" | "string" | "bool" | "array"
    enum: list[str] | None = None  # for fields with a fixed value set


class RuleVocabularyAction(BaseModel):
    """One action type with its argument schema for the builder."""

    model_config = ConfigDict(from_attributes=True)

    type: str  # "set_severity" | "add_tag" | "queue_optimization" | "notify"
    label: str
    args_schema: dict[str, Any]  # JSON schema-ish, keyed by arg name


class RuleVocabularyRead(BaseModel):
    """Everything the visual rule builder needs to render in a single call.

    Stage 15: surfaces the SUPPORTED_FIELDS, op sets, severity scale,
    and action types defined in :mod:`app.rules.schema`. The frontend
    builder reads this once at mount and renders typed inputs per
    condition row from it.
    """

    model_config = ConfigDict(from_attributes=True)

    fields: list[RuleVocabularyField]
    # ``ops`` is keyed by field type. The builder looks up valid ops
    # via ``ops[field.type]`` when rendering an operator dropdown.
    ops: dict[str, list[str]]
    severities: list[str]
    actions: list[RuleVocabularyAction]


# ── Stage 24: import/export ──────────────────────────────────


class RuleExportEntry(BaseModel):
    """One rule in a portable export bundle.

    Volatile per-instance state — ``id``, timestamps, evaluation
    counters — is deliberately excluded. Two Auditarr instances
    importing the same export should land identical rules; the
    bundle is content-addressable by ``(name, definition)``.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=10000)
    definition: dict[str, Any]


class RuleExportBundle(BaseModel):
    """Container for an exported rule set.

    ``version`` is a coarse schema tag, not the app version. Bump it
    when the bundle shape changes incompatibly. Importers MAY reject
    bundles whose ``version`` they don't recognize.
    """

    model_config = ConfigDict(extra="forbid")

    version: str = "1"
    exported_at: _dt.datetime
    rules: list[RuleExportEntry]


class RuleImportRequest(BaseModel):
    """Body for ``POST /api/v1/rules/import``.

    The ``on_conflict`` strategy decides what happens when a rule
    with the same ``name`` already exists:

    - ``skip``: existing rule stays, imported one is reported skipped
    - ``rename``: imported rule gets a unique suffix and is created
      alongside the existing one (default — safest)
    - ``overwrite``: existing rule's definition / description /
      priority / enabled are replaced by the imported values; the
      rule keeps its id and any associated evaluation history
    """

    model_config = ConfigDict(extra="forbid")

    bundle: RuleExportBundle
    on_conflict: str = Field(
        default="rename", pattern=r"^(skip|rename|overwrite)$"
    )


class RuleImportOutcome(BaseModel):
    """Per-rule report from an import. The frontend renders one
    row per outcome so the operator sees exactly what happened to
    each rule rather than a single aggregate count."""

    name: str
    final_name: str
    action: str  # "created" | "skipped" | "renamed" | "overwritten" | "error"
    rule_id: str | None = None
    error: str | None = None


class RuleImportResponse(BaseModel):
    created: int
    skipped: int
    renamed: int
    overwritten: int
    errors: int
    outcomes: list[RuleImportOutcome]
