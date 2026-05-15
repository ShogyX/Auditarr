/**
 * Stage 4 — Rule editor tab strip.
 *
 * The visible Visual / Dry-run / JSON tab buttons plus the inline
 * JSON-parse error indicator. Tab content is rendered by the parent
 * so the body's conditional rendering of ``VisualRuleBuilder`` /
 * ``DryRunPanel`` / JSON textarea stays in one place.
 *
 * Preserves the inline ``.rule-tab-strip`` + ``.rule-tab.is-active``
 * CSS contract used by the existing editor tests.
 */

import { cn } from "@/lib/cn";

import type { EditorTab } from "./editorShared";

export interface RuleEditorTabStripProps {
  tab: EditorTab;
  onTab: (next: EditorTab) => void;
  /** Inline JSON error text to surface to the right of the tabs.
   *  Empty / null means "JSON is valid". */
  jsonError?: string | null;
}

export function RuleEditorTabStrip({
  tab,
  onTab,
  jsonError,
}: RuleEditorTabStripProps) {
  return (
    <div className="rule-tab-strip">
      {(
        [
          ["visual", "Visual"],
          ["dryrun", "Dry-run"],
          // Stage 14b (audit follow-up): new tab between Dry-run
          // and JSON. Lists files this rule has matched, joined
          // to media_files for path / filename / severity. Click-
          // through deep-links to the Files page drawer via the
          // ``?file_id=`` URL param (see useFilesPageState).
          ["matched", "Matched files"],
          ["json", "JSON"],
        ] as const
      ).map(([key, label]) => (
        <button
          key={key}
          type="button"
          onClick={() => onTab(key)}
          className={cn("rule-tab", tab === key && "is-active")}
        >
          {label}
        </button>
      ))}
      <div className="flex-1" />
      {jsonError ? (
        <span className="text-[11.5px] text-sev-error">{jsonError}</span>
      ) : null}
    </div>
  );
}
