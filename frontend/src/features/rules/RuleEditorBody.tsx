/**
 * Stage 4 — Rule editor body.
 *
 * The interactive form for a rule (existing or new). Composes the
 * Stage 4 editor sub-modules:
 *
 *   - ``useRuleEditorState``   — all form state + save logic
 *   - ``RuleEditorTabStrip``   — Visual / Dry-run / JSON tabs
 *   - ``VisualRuleBuilder``    — the tree editor for ``definition.match``
 *   - ``DryRunPanel``          — test against an existing file
 *   - ``Field``                — uppercase form-row label wrapper
 *
 * Lifted into its own file so the route guard in ``RuleEditorPage``
 * can short-circuit loading / not-found before any of this state
 * initializes.
 *
 * Read-only mode (built-in rules) is reflected by ``readOnly`` from
 * the state hook: every input is disabled, Save is hidden in favour
 * of "Duplicate as custom rule", and the Visual tab falls back to a
 * pointer to JSON + Duplicate (the Visual builder has no clean
 * disabled-render mode).
 */

import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/Button";
import { Card, CardBody } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { ErrorState, LoadingState } from "@/components/ui/States";
import type { Rule } from "@/hooks/useRules";
import { cn } from "@/lib/cn";

import { DryRunPanel } from "./DryRunPanel";
import { Field } from "./editorShared";
import { MatchedFilesTab } from "./MatchedFilesTab";
import { RuleEditorTabStrip } from "./RuleEditorTabStrip";
import { useRuleEditorState } from "./useRuleEditorState";
import { VisualRuleBuilder } from "./VisualRuleBuilder";

export interface RuleEditorBodyProps {
  rule: Rule | null;
  onDone: () => void;
}

export function RuleEditorBody({ rule, onDone }: RuleEditorBodyProps) {
  const s = useRuleEditorState({ rule, onDone });

  return (
    <>
      <PageHeader
        title={s.title}
        helpKey="rules.conditions"
        actions={
          <>
            {/* Back button — short, unobtrusive, always present.
                Same role as the modal's Cancel: get me out of
                here without saving. */}
            <Button size="sm" variant="ghost" onClick={onDone}>
              <Icon name="arrow_left" size={12} />
              <span className="ml-1">Back</span>
            </Button>
            {s.readOnly ? (
              // Built-in path. Save would be a 422 anyway (the
              // backend blocks rename/description/definition);
              // hide it so the affordance set matches reality.
              // Duplicate is the primary CTA — same framing the
              // Rules list uses on Stage 29.
              <Button
                size="sm"
                variant="primary"
                onClick={s.onDuplicate}
                disabled={s.duplicateMutation.isPending}
                title="Built-in rules are read-only. Duplicate to create an editable custom variant."
              >
                <Icon name="duplicate" size={12} />
                <span className="ml-1">
                  {s.duplicateMutation.isPending
                    ? "Duplicating…"
                    : "Duplicate as custom rule"}
                </span>
              </Button>
            ) : (
              <Button
                size="sm"
                variant="accent"
                onClick={() => s.formRef.current?.requestSubmit()}
                disabled={s.isPending || !s.parsedDefinition.ok}
              >
                <Icon name={rule ? "check" : "plus"} size={12} />
                <span className="ml-1">
                  {s.isPending ? "Saving…" : rule ? "Save" : "Create"}
                </span>
              </Button>
            )}
          </>
        }
      />

      <div className="p-6 flex flex-col gap-4 max-w-7xl rule-editor-shell">
        {s.isBuiltin ? (
          // A subtle banner so an operator who landed here from
          // a deep link knows why the inputs are disabled. The
          // tooltip on "Duplicate as custom rule" repeats this,
          // but seeing it up-front avoids a "wait, why can't I
          // type?" moment.
          <Card>
            <CardBody>
              <div className="flex items-start gap-3">
                <Icon name="info" size={14} className="text-sev-info mt-0.5" />
                <div className="text-[12.5px]">
                  <span className="font-medium">This is a built-in rule.</span>{" "}
                  Auditarr ships it with the codebase, so its body and name are
                  read-only. You can still toggle it from the Rules list, or{" "}
                  <button
                    type="button"
                    className="text-accent underline"
                    onClick={s.onDuplicate}
                    disabled={s.duplicateMutation.isPending}
                  >
                    duplicate it
                  </button>{" "}
                  to get a writable custom copy.
                </div>
              </div>
            </CardBody>
          </Card>
        ) : null}

        <Card>
          <form
            ref={s.formRef}
            onSubmit={s.onSubmit}
            className="flex flex-col gap-3.5 p-4"
          >
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label="Name">
                <input
                  required
                  value={s.name}
                  onChange={(e) => s.setName(e.target.value)}
                  placeholder="Flag big HEVC files"
                  className="settings-input"
                  disabled={s.readOnly}
                />
              </Field>
              <Field label="Priority">
                <input
                  type="number"
                  min={0}
                  max={10000}
                  value={s.priority}
                  onChange={(e) => s.setPriority(Number(e.target.value))}
                  className="settings-input"
                  // Builtin: priority IS operator-tunable per the
                  // Stage 29 API contract, but it lives in the
                  // Rules-list row, not the editor. Disabling it
                  // here keeps the read-only mode coherent.
                  disabled={s.readOnly}
                />
                {/* Stage 4 audit fix (Issue 20): priority semantics
                    are not obvious from the input alone. The actual
                    backend behaviour (see rules_service.evaluate_file)
                    is that lower priority numbers iterate first AND
                    every matched rule still applies — severity
                    escalates to the highest matched. The hint mirrors
                    that contract rather than the "first match wins"
                    shorthand. */}
                <span className="text-[11.5px] text-muted-2">
                  Lower numbers run first. All matching rules apply;
                  the highest resulting severity wins.
                </span>
              </Field>
            </div>

            <Field label="Description (optional)">
              <input
                value={s.description}
                onChange={(e) => s.setDescription(e.target.value)}
                placeholder="What does this rule do?"
                className="settings-input"
                disabled={s.readOnly}
              />
            </Field>

            <label className="flex items-center gap-2 text-[13px]">
              <button
                type="button"
                role="switch"
                aria-checked={s.enabled}
                onClick={() => !s.readOnly && s.setEnabled(!s.enabled)}
                className={cn(
                  "settings-switch",
                  s.enabled && "is-on",
                  s.readOnly && "opacity-60 cursor-not-allowed",
                )}
                disabled={s.readOnly}
              >
                <span className="settings-switch-thumb" />
              </button>
              <span>Enabled</span>
            </label>

            <RuleEditorTabStrip
              tab={s.tab}
              onTab={s.setTab}
              jsonError={s.parsedDefinition.ok ? null : s.parsedDefinition.error}
            />

            {s.tab === "visual" ? (
              s.vocabulary.isLoading ? (
                <LoadingState label="Loading vocabulary…" />
              ) : s.vocabulary.isError || !s.vocabulary.data ? (
                <ErrorState
                  title="Couldn't load rule vocabulary"
                  description={(s.vocabulary.error as Error)?.message}
                />
              ) : s.readOnly ? (
                // The Visual builder is interactive — every drag,
                // dropdown, and add-condition button mutates the
                // definition. There's no clean "render but disable
                // every input" mode that's worth building for the
                // builtin path. Instead we point the operator at
                // the JSON tab, which IS readable in read-only
                // mode, and offer Duplicate as the path to editing.
                <div className="rounded-md border border-border bg-surface-sunk p-4 text-[12.5px] text-muted-2 flex flex-col gap-2">
                  <div>
                    The Visual builder is only available for custom rules.
                    Switch to the <strong>JSON</strong> tab to inspect this
                    rule's definition, or use the <strong>Dry-run</strong> tab
                    to test it against a file.
                  </div>
                  <div>
                    To edit it, duplicate to a custom rule first using the
                    button above.
                  </div>
                </div>
              ) : (
                <VisualRuleBuilder
                  definition={s.definition}
                  vocabulary={s.vocabulary.data}
                  onChange={s.commitFromVisual}
                />
              )
            ) : null}

            {s.tab === "dryrun" ? (
              <DryRunPanel definition={s.definition} />
            ) : null}

            {/* Stage 14b (audit follow-up): per-rule matched-files
                listing. Hidden when the editor is on the "new rule"
                route (no rule id yet — nothing can have matched). */}
            {s.tab === "matched" ? (
              rule ? (
                <MatchedFilesTab ruleId={rule.id} />
              ) : (
                <div className="text-[12.5px] text-muted-2">
                  Save the rule first to see matched files.
                </div>
              )
            ) : null}

            {s.tab === "json" ? (
              <Field label="Definition (JSON)">
                <textarea
                  required
                  value={s.definitionText}
                  onChange={(e) => s.commitFromJson(e.target.value)}
                  spellCheck={false}
                  // The page editor gets more vertical room than
                  // the modal had — 24 rows fits a moderately
                  // nested rule without scrolling.
                  rows={24}
                  className={cn(
                    "px-2 py-2 text-[12.5px] font-mono bg-surface-sunk border rounded-md",
                    "focus:outline-none focus:ring-2 focus:ring-accent resize-y",
                    s.parsedDefinition.ok
                      ? "border-border"
                      : "border-sev-error",
                  )}
                  readOnly={s.readOnly}
                />
                <span className="text-[11.5px] text-muted-2">
                  See the Help drawer (Cmd/Ctrl+/) for the rule schema
                  reference.
                </span>
              </Field>
            ) : null}

            {s.error ? (
              <div className="text-[12px] text-sev-error">{s.error}</div>
            ) : null}
          </form>
        </Card>
      </div>
    </>
  );
}
