/**
 * Stage 4 — Rules page (slim orchestrator).
 *
 * State, derivations, and row-action handlers live in
 * ``useRulesPageState``. Sub-components live next to this file. The
 * page itself composes:
 *
 *   - ``PageHeader``        — title / subtitle / evaluate bar / New rule
 *   - ``RulesEvaluateBar``  — library picker + Evaluate button
 *   - ``RulesTabBar``       — Custom / Built-in / Suggestions / Automation
 *   - ``RulesToolbar``      — search + Import / Export (Custom tab only)
 *   - ``RulesTable``        — table + row rendering (both tabs)
 *   - ``SuggestionsCard``   — re-used from features/dashboard
 *   - ``AutomationTabContent`` — re-used from features/automation
 *   - ``ImportRulesDialog`` — bundle import
 *   - ``SuggestionReviewModal`` — re-used from features/dashboard
 *
 * Stage 10 audit fix (Issue 15): Automation merged in as a tab.
 * The previous standalone /automation route now redirects to
 * /rules?tab=automation. Tab state is URL-driven via
 * ``useRulesPageState`` so the redirect lands on the right tab.
 *
 * Pre-Stage-4:  720 LOC
 * Post-Stage-4: ~135 LOC (this file)
 */

import { useNavigate } from "react-router-dom";

import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { AutomationTabContent } from "@/features/automation/AutomationTabContent";
import { SuggestionReviewModal } from "@/features/dashboard/SuggestionReviewModal";
import { SuggestionsCard } from "@/features/dashboard/SuggestionsCard";
import { useHelpKey } from "@/hooks/useHelpKey";

import { ImportRulesDialog } from "./ImportRulesDialog";
import { RulesEvaluateBar } from "./RulesEvaluateBar";
import { RulesTabBar } from "./RulesTabBar";
import { RulesTable } from "./RulesTable";
import { RulesToolbar } from "./RulesToolbar";
import { useRulesPageState } from "./useRulesPageState";

export function RulesPage() {
  useHelpKey("rules.conditions");
  const navigate = useNavigate();
  const s = useRulesPageState();

  // Stage 2: header actions split by tab.
  //   - non-automation tabs → RulesEvaluateBar + "New rule"
  //   - automation tab      → "New schedule"
  // Previously the entire header CTA block was suppressed on the
  // Automation tab, which left operators bouncing into the body to
  // find the New-schedule affordance and made the page look
  // half-empty. The body still renders its own "New schedule"
  // button when AutomationTabContent is used standalone via the
  // legacy /automation page; on the merged Rules surface we pass
  // ``hideInlineNewScheduleButton`` so the header copy is the only
  // one rendered.
  const isAutomationTab = s.tab === "automation";

  return (
    <>
      <PageHeader
        title="Rules"
        sub="Conditions that decide severity and tags for every file in your library"
        helpKey="rules.conditions"
        actions={
          isAutomationTab ? (
            <Button
              size="sm"
              variant="primary"
              onClick={() => s.setCreatingSchedule(true)}
              disabled={s.jobKindsLoading}
            >
              <Icon name="plus" size={12} />
              <span className="ml-1">New schedule</span>
            </Button>
          ) : (
            <>
              <RulesEvaluateBar
                libraries={s.libraries.data ?? []}
                selectedLibrary={s.selectedLibrary}
                onSelectLibrary={s.setSelectedLibrary}
                onEvaluate={() => s.evaluate.mutate(s.selectedLibrary)}
                isEvaluating={s.evaluate.isPending}
              />
              <Button
                size="sm"
                variant="primary"
                onClick={() => navigate("/rules/new")}
              >
                <Icon name="plus" size={12} />
                <span className="ml-1">New rule</span>
              </Button>
            </>
          )
        }
      />

      <div className="p-6 flex flex-col gap-4 max-w-5xl">
        {s.evaluate.data && s.tab !== "automation" ? (
          <Card>
            <div className="px-4 py-3 text-[13px]">
              Re-evaluated {s.evaluate.data.files_evaluated} file(s) in the
              selected library.
            </div>
          </Card>
        ) : null}

        <Card>
          {/* Tab strip + toolbar. The tabs use the same segmented vocab
              as the scope bar so the look is consistent across pages. */}
          <div className="rules-toolbar">
            <RulesTabBar
              tab={s.tab}
              onTab={s.setTab}
              customCount={s.customRules.length}
              builtinCount={s.builtinRules.data?.length ?? 0}
              suggestionsCount={s.pendingSuggestionsCount}
            />

            {s.tab === "custom" ? (
              <RulesToolbar
                search={s.search}
                onSearch={s.setSearch}
                onImport={() => s.setImporting(true)}
                ruleCount={s.rules.data?.length ?? 0}
              />
            ) : null}
          </div>

          {s.tab === "custom" ? (
            <RulesTable
              variant="custom"
              query={s.rules}
              visibleRules={s.visibleRules}
              onEdit={(r) => navigate(`/rules/${r.id}/edit`)}
              onToggle={s.onToggle}
              onDuplicate={s.onDuplicate}
              onDelete={s.onDelete}
            />
          ) : null}

          {s.tab === "builtin" ? (
            <RulesTable
              variant="builtin"
              query={s.builtinRules}
              // Stage 30: built-in rows now navigate to the routed
              // editor in read-only mode. The editor's banner +
              // disabled inputs make the read-only status visible;
              // Duplicate is the primary CTA there.
              visibleRules={s.builtinRules.data ?? []}
              onEdit={(r) => navigate(`/rules/${r.id}/edit`)}
              onToggle={s.onToggle}
              onDuplicate={s.onDuplicate}
            />
          ) : null}

          {s.tab === "suggestions" ? (
            <div className="p-4">
              <SuggestionsCard onReview={(sug) => s.setReviewing(sug)} />
            </div>
          ) : null}

          {/* Stage 10 audit fix (Issue 15): Automation tab body.
              Reuses the same component the standalone /automation
              route renders, so the two surfaces stay in sync.
              Stage 2: controlled-mode — the header's "New schedule"
              button drives the dialog via URL state, and the body's
              inline button is suppressed so there's only one CTA. */}
          {isAutomationTab ? (
            <div className="p-4">
              <AutomationTabContent
                creating={s.creatingSchedule}
                onCreatingChange={s.setCreatingSchedule}
                hideInlineNewScheduleButton
              />
            </div>
          ) : null}
        </Card>
      </div>

      {/* Stage 30: the RuleDialog modal is gone. Rules edit / create
          lives at /rules/new and /rules/:ruleId/edit. */}
      {s.importing ? (
        <ImportRulesDialog onClose={() => s.setImporting(false)} />
      ) : null}
      {s.reviewing ? (
        <SuggestionReviewModal
          suggestion={s.reviewing}
          onClose={() => s.setReviewing(null)}
        />
      ) : null}
    </>
  );
}
