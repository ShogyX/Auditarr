"""Rule suggestion schemas (Stage 16 Turn 2)."""

from __future__ import annotations

import datetime as _dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RuleSuggestionRead(BaseModel):
    """One pending or historical suggestion returned to the frontend."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    definition: dict[str, Any]
    heuristic: str
    evidence: dict[str, Any]
    files_affected: int
    est_runtime_s: int | None
    confidence: float
    dedup_key: str
    status: str
    deployed_rule_id: str | None
    deployed_at: _dt.datetime | None
    dismissed_at: _dt.datetime | None
    dismissed_reason: str | None
    created_at: _dt.datetime


class SuggestionDeployRequest(BaseModel):
    """Optional patch applied to the definition before it's saved as
    a rule. The frontend opens the visual builder pre-populated with
    the suggestion's definition; if the operator edits it, they post
    the modified version here. ``name`` lets the operator rename the
    rule away from the analyzer's default phrasing."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=1024)
    priority: int | None = Field(default=None, ge=0, le=10_000)
    enabled: bool | None = None
    definition: dict[str, Any] | None = None


class SuggestionDismissRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=1024)


class AnalyzePlaybackRunResponse(BaseModel):
    examined_events: int
    candidates_generated: int
    suggestions_created: int
    skipped_deduped: int
    skipped_dismissed: int
    skipped_deployed: int
    skipped_too_few_events: bool
