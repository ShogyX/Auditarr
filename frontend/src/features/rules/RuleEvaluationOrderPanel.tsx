/**
 * v1.9 Stage 4.5 — Rule priority queue side panel.
 *
 * The rule editor grows a side panel ("Evaluation order") that
 * lists every enabled rule sorted by priority, highlighting where
 * the rule currently being edited sits. Click a row to navigate
 * to that rule's editor.
 *
 * Why this matters: rule priority semantics aren't obvious. The
 * Priority field's hint already explains "lower runs first, all
 * matching apply, highest severity wins" — but operators still
 * lose the bigger picture: where does THIS rule sit in the queue?
 * If I bump its priority from 50 to 30, which rules am I jumping?
 * The side panel makes that visible without leaving the editor.
 *
 * Design choices:
 *   * Enabled rules only. Disabled rules don't evaluate, so they
 *     don't belong in the visible priority queue. (The Rules list
 *     page is where the operator manages enable/disable.)
 *   * The currently-edited rule is highlighted regardless of its
 *     enabled state — even if the operator just disabled it, they
 *     should still see "this is where it WAS in the order".
 *   * The new rule case ("/rules/new") shows the panel with no
 *     current-rule highlight; rows below where this new rule will
 *     land (priority field on the form) get a subtle marker so
 *     the operator can pick a slot.
 *   * Click handler navigates via react-router; uses ``replace`` so
 *     the back button doesn't accumulate a stack of editor
 *     transitions.
 */

import { useMemo } from "react";
import { useNavigate } from "react-router-dom";

import { Card, CardBody, CardHead } from "@/components/ui/Card";
import { LoadingState, EmptyState } from "@/components/ui/States";
import { useRules, type Rule } from "@/hooks/useRules";
import { cn } from "@/lib/cn";

export interface RuleEvaluationOrderPanelProps {
  /** The currently-edited rule's id. ``null`` for the create
   *  path — the panel renders without a highlight but still
   *  shows where the operator's chosen ``currentPriority`` would
   *  insert. */
  currentRuleId: string | null;
  /** The current value of the Priority input. Used to show an
   *  insertion-point marker on the create path; ignored when
   *  ``currentRuleId`` is present (the existing rule's own
   *  highlight serves the same purpose). */
  currentPriority: number;
}

export function RuleEvaluationOrderPanel({
  currentRuleId,
  currentPriority,
}: RuleEvaluationOrderPanelProps) {
  const navigate = useNavigate();
  const rulesQuery = useRules();

  // Sort enabled rules by priority asc (lower runs first).
  // The currently-edited rule stays in the list even if it's
  // disabled — operators need to see where it lives.
  const ordered = useMemo(() => {
    const all = rulesQuery.data ?? [];
    const visible = all.filter(
      (r) => r.enabled || r.id === currentRuleId,
    );
    visible.sort((a, b) => {
      if (a.priority !== b.priority) return a.priority - b.priority;
      // Stable secondary sort by name so identical-priority rules
      // don't reorder between renders.
      return a.name.localeCompare(b.name);
    });
    return visible;
  }, [rulesQuery.data, currentRuleId]);

  // For the create path, find the insertion index based on
  // currentPriority. The marker renders before the first rule
  // with priority >= currentPriority.
  const insertionIndex = useMemo(() => {
    if (currentRuleId !== null) return -1;
    return ordered.findIndex((r) => r.priority >= currentPriority);
  }, [ordered, currentPriority, currentRuleId]);

  return (
    <Card>
      <CardHead
        title="Evaluation order"
        subtitle="Enabled rules, ordered by priority. Lower runs first."
      />
      <CardBody>
        {rulesQuery.isLoading ? (
          <LoadingState label="Loading rules…" />
        ) : rulesQuery.isError ? (
          // Fail-soft: the side panel is a help affordance, not
          // critical to saving the rule. Show a tiny message and
          // let the editor proceed.
          <div className="text-[12px] text-sev-warn">
            Couldn't load rule list for evaluation-order preview.
          </div>
        ) : ordered.length === 0 ? (
          <EmptyState
            icon="info"
            title="No enabled rules"
            description="Enable a rule from the Rules list to populate this preview."
          />
        ) : (
          <ul className="list-none m-0 p-0 flex flex-col">
            {ordered.map((rule, idx) => {
              const isCurrent = rule.id === currentRuleId;
              const showInsertionAbove =
                currentRuleId === null && idx === insertionIndex;
              return (
                <li key={rule.id} className="contents">
                  {showInsertionAbove ? <InsertionMarker /> : null}
                  <RowEntry
                    rule={rule}
                    isCurrent={isCurrent}
                    onJump={() => {
                      // ``replace`` to avoid stacking editor
                      // history entries — operators who jump
                      // around shouldn't have to press Back N
                      // times to escape the editor.
                      navigate(`/rules/${rule.id}/edit`, { replace: true });
                    }}
                  />
                </li>
              );
            })}
            {/* Insertion marker at the END for the case where the
                operator's chosen priority is higher than every
                existing rule. */}
            {currentRuleId === null && insertionIndex === -1 ? (
              <InsertionMarker />
            ) : null}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

function RowEntry({
  rule,
  isCurrent,
  onJump,
}: {
  rule: Rule;
  isCurrent: boolean;
  onJump: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onJump}
      disabled={isCurrent}
      className={cn(
        "flex items-center gap-2 px-2 py-1.5 rounded text-left text-[12px]",
        isCurrent
          ? "bg-accent/15 text-text cursor-default border-l-2 border-accent"
          : "hover:bg-[var(--hover)] text-text-2",
      )}
      aria-current={isCurrent ? "true" : undefined}
      title={isCurrent ? "Currently editing" : "Jump to this rule"}
    >
      <span className="font-mono text-[11px] tabular-nums text-muted-2 w-9 text-right shrink-0">
        {rule.priority}
      </span>
      <span className="flex-1 truncate">{rule.name}</span>
      {!rule.enabled && isCurrent ? (
        <span className="text-[10px] uppercase tracking-wide text-muted-2 shrink-0">
          disabled
        </span>
      ) : null}
    </button>
  );
}

function InsertionMarker() {
  // v1.9 Stage 4.5 — for the create path, indicate where the
  // new rule will land based on the operator's Priority input.
  // A thin accent rule + a tiny label.
  return (
    <div
      className="flex items-center gap-2 px-2 py-1"
      aria-hidden="true"
    >
      <div className="flex-1 h-px bg-accent" />
      <span className="text-[10px] uppercase tracking-wide text-accent font-semibold shrink-0">
        new rule here
      </span>
      <div className="flex-1 h-px bg-accent" />
    </div>
  );
}
