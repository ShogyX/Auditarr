"""v1.9 Stage 9.2 — stale rule analyzer.

Complement to ``PlaybackAnalyzer``: rather than suggesting new
rules, this module surfaces existing rules that look stale and
might benefit from being disabled or removed.

Two heuristics:

  * **Inactive**: a rule that's been evaluated recently (so
    Auditarr has been running) but matched zero files on each
    of its last evaluations. Either the operator's library no
    longer contains the targeted files, or the rule's
    conditions are too narrow to ever trigger.

  * **Overzealous**: a rule fires on a high proportion of
    direct-play sessions (the device handles those files
    without transcoding, so a "transcode this codec" rule
    matches files the device doesn't need re-encoded). The
    suggestion is to lower the rule's severity rather than
    delete it — the operator might still want a warning,
    just not a critical flag.

Each heuristic produces a ``StaleRuleSuggestion`` carrying:
  * the affected ``rule_id`` and ``rule_name``
  * the heuristic kind (``"inactive"`` | ``"overzealous"``)
  * a short ``reason`` string for operator display
  * structured ``evidence`` with the counts / window for
    transparency.

The output is read-only — operators decide whether to act.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.playback import PlaybackEvent
from app.models.playback_device import PlaybackDevice
from app.models.rule import Rule
from app.utils.datetime import utcnow

# Heuristic thresholds. Conservative — false positives cost
# operator trust more than missed signals.
INACTIVE_WINDOW_DAYS = 30
OVERZEALOUS_DIRECT_PLAY_RATIO = 0.6
OVERZEALOUS_MIN_SAMPLES = 20


@dataclass(slots=True)
class StaleRuleSuggestion:
    """One stale-rule observation. Read-only — operators decide
    what to do (disable, lower severity, delete)."""

    rule_id: str
    rule_name: str
    heuristic: str  # "inactive" | "overzealous"
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "heuristic": self.heuristic,
            "reason": self.reason,
            "evidence": self.evidence,
        }


@dataclass(slots=True)
class StaleRuleAnalysisOutcome:
    suggestions: list[StaleRuleSuggestion] = field(default_factory=list)
    rules_examined: int = 0


class StaleRuleAnalyzer:
    """Run the two stale-rule heuristics. Stateless except for
    the session reference."""

    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session

    async def analyze(self) -> StaleRuleAnalysisOutcome:
        outcome = StaleRuleAnalysisOutcome()
        rules = (
            (
                await self._session.execute(
                    select(Rule).where(Rule.enabled.is_(True))
                )
            )
            .scalars()
            .all()
        )
        outcome.rules_examined = len(rules)

        # Aggregate device transcode signal for the "overzealous"
        # heuristic. A rule that ostensibly targets a codec for
        # transcode but where most observed plays of that codec
        # were direct_play is a candidate for severity-lowering.
        # We use the device index for this (Stage 9.1) — it
        # aggregates decisions per device so the join is cheap.
        device_rows = (
            (await self._session.execute(select(PlaybackDevice)))
            .scalars()
            .all()
        )
        # If we have no device signal at all, skip the overzealous
        # heuristic. The inactive heuristic still runs.
        device_signal_available = bool(device_rows)

        # v1.9 audit fix (STALE-1): compute the direct-play ratio
        # ONCE for the entire analyzer run rather than re-querying
        # all playback events per rule. The previous code was
        # O(N*M) — 50 rules × 10k events = 500k row materializations
        # per run. Now O(N + M).
        direct_play_signal: tuple[float, int] | None = None
        if device_signal_available:
            direct_play_signal = await self._compute_direct_play_ratio()

        for rule in rules:
            if _is_inactive(rule):
                outcome.suggestions.append(
                    StaleRuleSuggestion(
                        rule_id=rule.id,
                        rule_name=rule.name,
                        heuristic="inactive",
                        reason=(
                            "Rule has been evaluated recently but matched "
                            "zero files on its most recent run."
                        ),
                        evidence={
                            "last_evaluated_at": (
                                rule.last_evaluated_at.isoformat()
                                if rule.last_evaluated_at
                                else None
                            ),
                            "last_match_count": rule.last_match_count,
                        },
                    )
                )

            if direct_play_signal is not None:
                overzealous = self._check_overzealous(
                    rule, direct_play_signal=direct_play_signal
                )
                if overzealous is not None:
                    outcome.suggestions.append(overzealous)

        return outcome

    async def _compute_direct_play_ratio(self) -> tuple[float, int]:
        """v1.9 audit fix (STALE-1): a single grouped query that
        returns ``(direct_play_ratio, total_samples)`` for the
        analysis window. Each rule's overzealous check then
        consults this cached pair rather than re-querying.

        Uses SQL aggregation rather than materializing all rows
        — fast even when the events table is large."""
        from sqlalchemy import case as sql_case

        cutoff = utcnow() - _dt.timedelta(days=INACTIVE_WINDOW_DAYS)
        result = await self._session.execute(
            select(
                func.count().label("total"),
                func.sum(
                    sql_case(
                        (PlaybackEvent.decision == "direct_play", 1),
                        else_=0,
                    )
                ).label("dp"),
            ).where(PlaybackEvent.started_at >= cutoff)
        )
        row = result.one()
        total = int(row.total or 0)
        direct_play = int(row.dp or 0)
        if total == 0:
            return (0.0, 0)
        return (direct_play / total, total)

    def _check_overzealous(
        self,
        rule: Rule,
        *,
        direct_play_signal: tuple[float, int],
    ) -> StaleRuleSuggestion | None:
        """Heuristic 2: if the rule's matched files are mostly
        being direct-played by observed devices, lowering its
        severity is a reasonable next step.

        Implementation: we don't have per-file rule-match
        tracking yet, so we approximate by asking "across all
        observed events in the analysis window, what fraction
        had decision=direct_play?". When that ratio is high
        AND the rule has been firing (last_match_count > 0),
        we flag it.

        This is a conservative heuristic — false-positive
        suggestions cost operator trust. We don't act on
        anything below MIN_SAMPLES.

        v1.9 audit fix (STALE-1): pure / synchronous — the
        ratio is computed once in ``analyze`` and passed in."""
        if rule.last_match_count <= 0:
            # Not firing — covered by the inactive heuristic if
            # applicable; we don't double-flag.
            return None

        ratio, samples = direct_play_signal
        if samples < OVERZEALOUS_MIN_SAMPLES:
            return None
        if ratio < OVERZEALOUS_DIRECT_PLAY_RATIO:
            return None

        return StaleRuleSuggestion(
            rule_id=rule.id,
            rule_name=rule.name,
            heuristic="overzealous",
            reason=(
                "Observed devices are direct-playing the majority of files "
                "in the analysis window. Consider lowering the rule's "
                "severity rather than removing it."
            ),
            evidence={
                "direct_play_ratio": round(ratio, 3),
                "samples": samples,
                "window_days": INACTIVE_WINDOW_DAYS,
                "rule_match_count": rule.last_match_count,
            },
        )


def _is_inactive(rule: Rule) -> bool:
    """True when the rule was evaluated within the analysis
    window but matched zero files. We need the evaluation
    timestamp to confirm Auditarr is actually running the rule
    — a rule whose engine has been off for a year shouldn't be
    flagged stale just because it hasn't been seen recently."""
    if rule.last_evaluated_at is None:
        return False
    last_eval = rule.last_evaluated_at
    if last_eval.tzinfo is None:
        last_eval = last_eval.replace(tzinfo=_dt.UTC)
    cutoff = utcnow() - _dt.timedelta(days=INACTIVE_WINDOW_DAYS)
    if last_eval < cutoff:
        # Engine appears to have not been running — don't suggest
        # removal; this is an engine-config problem, not a rule
        # problem.
        return False
    return rule.last_match_count == 0


__all__ = [
    "StaleRuleAnalyzer",
    "StaleRuleAnalysisOutcome",
    "StaleRuleSuggestion",
    "INACTIVE_WINDOW_DAYS",
    "OVERZEALOUS_DIRECT_PLAY_RATIO",
    "OVERZEALOUS_MIN_SAMPLES",
]
