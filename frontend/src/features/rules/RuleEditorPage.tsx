/**
 * Stage 30 — Routed full-screen rule editor (Stage 4 slimmed).
 *
 * This file is now a thin route guard. The interactive editor lives
 * in ``RuleEditorBody``; the route resolves the rule id (or "new")
 * and short-circuits loading / error / not-found before any of the
 * body's state hooks run. That keeps the form-state initialization
 * from re-running with stale data during the initial fetch and
 * preserves the existing test contract (which asserts on
 * "no rule fetch on /rules/new" + "Rule not found" empty state).
 *
 * Routes:
 *   /rules/new           — create
 *   /rules/:ruleId/edit  — edit existing (custom or built-in)
 *
 * Pre-Stage-4:  668 LOC
 * Post-Stage-4: ~65 LOC (this file)
 */

import { useNavigate, useParams } from "react-router-dom";

import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/Button";
import { Card, CardBody } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { EmptyState, LoadingState } from "@/components/ui/States";
import { useHelpKey } from "@/hooks/useHelpKey";
import { useRule } from "@/hooks/useRules";

import { RuleEditorBody } from "./RuleEditorBody";

export function RuleEditorPage() {
  useHelpKey("rules.conditions");
  const navigate = useNavigate();
  const params = useParams<{ ruleId?: string }>();
  const isNew = !params.ruleId;

  // Fetch the rule on edit; on create we skip the query.
  const ruleQuery = useRule(params.ruleId);

  if (isNew) {
    // Skip the loading branch entirely for the create path —
    // there's nothing to fetch.
    return <RuleEditorBody rule={null} onDone={() => navigate("/rules")} />;
  }

  if (ruleQuery.isLoading) {
    return (
      <>
        <PageHeader title="Edit rule" helpKey="rules.conditions" />
        <div className="p-6">
          <Card>
            <CardBody>
              <LoadingState label="Loading rule…" />
            </CardBody>
          </Card>
        </div>
      </>
    );
  }
  if (ruleQuery.isError || !ruleQuery.data) {
    return (
      <>
        <PageHeader title="Edit rule" helpKey="rules.conditions" />
        <div className="p-6">
          <Card>
            <CardBody>
              <EmptyState
                icon="rules"
                title="Rule not found"
                description={
                  ruleQuery.isError
                    ? (ruleQuery.error as Error).message
                    : "This rule may have been deleted from another tab."
                }
              />
              <div className="mt-3 flex justify-center">
                <Button onClick={() => navigate("/rules")}>
                  <Icon name="arrow_left" size={12} />
                  <span className="ml-1">Back to rules</span>
                </Button>
              </div>
            </CardBody>
          </Card>
        </div>
      </>
    );
  }

  return (
    <RuleEditorBody
      rule={ruleQuery.data}
      onDone={() => navigate("/rules")}
    />
  );
}
