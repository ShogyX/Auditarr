/**
 * Stage 4 — Rule editor shared bits.
 *
 * The small primitives that used to live at the bottom of
 * ``RuleEditorPage.tsx``: the form-row ``Field`` label wrapper, the
 * ``DEFAULT_DEFINITION`` blank-rule body, and the editor's three-tab
 * vocabulary.
 *
 * Stage 6b: ``Field`` is now sourced from the cross-feature
 * ``components/ui/Field`` primitive promoted in Stage 6. The re-
 * export keeps existing callers (every file in ``features/rules/``
 * that imports ``Field`` from this module) working without an
 * import-path migration.
 */

import type { RuleDefinition } from "@/hooks/useRules";

export { Field } from "@/components/ui/Field";

/**
 * The default body for a brand-new rule — same shape as the old
 * dialog. Operators can replace it via the Visual or JSON tab; the
 * goal is "you can hit Save right now and not get a 422" rather than
 * "this is what you should match."
 */
export const DEFAULT_DEFINITION: RuleDefinition = {
  match: { field: "video_codec", op: "eq", value: "hevc" },
  actions: [{ type: "set_severity", severity: "warn" }],
};

export type EditorTab = "visual" | "dryrun" | "matched" | "json";
