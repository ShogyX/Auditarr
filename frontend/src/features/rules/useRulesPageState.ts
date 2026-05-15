/**
 * Stage 4 — Rules page state hook.
 *
 * Owns every piece of RulesPage state that used to live as inline
 * ``useState`` / ``useMemo`` in the page component:
 *
 *   - tab selection (custom / builtin / suggestions)
 *   - search box value
 *   - selected library for the Evaluate action (header)
 *   - import dialog open state
 *   - currently-reviewing suggestion (modal state)
 *
 * Also exposes the React Query results + mutation hooks the page
 * consumes so the orchestrator can stay declarative.
 *
 * Returns one big object because the page itself is the only
 * consumer; splitting into sub-hooks would just relocate the
 * boilerplate. Behaviour is preserved exactly so the existing tests
 * (``RulesPage.test.tsx``, ``RulesPage.stage29.test.tsx``) continue
 * to pass without modification.
 */

import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { useJobKinds } from "@/hooks/useAutomation";
import { useLibraries } from "@/hooks/useMedia";
import {
  useDeleteRule,
  useDuplicateRule,
  useEvaluateLibrary,
  useRuleSuggestions,
  useRules,
  useUpdateRule,
  type Rule,
  type RuleSuggestion,
} from "@/hooks/useRules";
import { toast } from "@/lib/toast";

import type { RulesTab } from "./rulesShared";

export interface UseRulesPageState {
  /* Queries. */
  rules: ReturnType<typeof useRules>;
  builtinRules: ReturnType<typeof useRules>;
  suggestions: ReturnType<typeof useRuleSuggestions>;
  libraries: ReturnType<typeof useLibraries>;

  /* Mutations. */
  remove: ReturnType<typeof useDeleteRule>;
  update: ReturnType<typeof useUpdateRule>;
  duplicate: ReturnType<typeof useDuplicateRule>;
  evaluate: ReturnType<typeof useEvaluateLibrary>;

  /* Tab + search + library-eval state. */
  tab: RulesTab;
  setTab: (t: RulesTab) => void;
  search: string;
  setSearch: (s: string) => void;
  selectedLibrary: string;
  setSelectedLibrary: (id: string) => void;

  /* Modal/dialog state. */
  importing: boolean;
  setImporting: (v: boolean) => void;
  reviewing: RuleSuggestion | null;
  setReviewing: (s: RuleSuggestion | null) => void;
  /* Stage 2: Automation "New schedule" dialog state lifted to URL.
   * ``?new=schedule`` opens the create dialog. The Rules page header
   * sets this when the user clicks New schedule on the Automation
   * tab; ``AutomationTabContent`` consumes it so the dialog state
   * isn't owned by either parent in isolation. URL-driven so a
   * refresh on ``/rules?tab=automation&new=schedule`` reopens the
   * dialog where the operator left it. */
  creatingSchedule: boolean;
  setCreatingSchedule: (v: boolean) => void;
  /* Stage 2: the header's "New schedule" button on the Automation
   * tab must disable while the job-kinds list is still loading
   * (clicking it would open a dialog whose kind-picker is empty).
   * Exposed here so the page-level header CTA can react without
   * importing the hook directly. */
  jobKindsLoading: boolean;

  /* Derived lists. */
  customRules: Rule[];
  visibleRules: Rule[];
  pendingSuggestionsCount: number;

  /* Row-action handlers (single source so tests can stub mutations
   * without re-implementing the error/toast contract). */
  onDuplicate: (rule: Rule) => Promise<void>;
  onToggle: (rule: Rule) => void;
  onDelete: (rule: Rule) => void;
}

export function useRulesPageState(): UseRulesPageState {
  const rules = useRules();
  // Stage 29: separate query for built-in rules. The primary
  // ``rules`` query returns the union; the Built-in tab queries
  // with ``is_builtin=true`` so the count badge reflects what the
  // server thinks belongs in that tab even if the union is paged.
  const builtinRules = useRules({ is_builtin: true });
  const suggestions = useRuleSuggestions();
  const libraries = useLibraries();
  // Stage 2: we need to know whether the Automation kinds list is
  // still loading so the header's "New schedule" button can disable
  // until the kind-picker can be populated.
  const jobKinds = useJobKinds();

  const remove = useDeleteRule();
  const update = useUpdateRule();
  const duplicate = useDuplicateRule();
  const evaluate = useEvaluateLibrary();

  const [tab, setTab] = useTabFromUrl();
  const [creatingSchedule, setCreatingSchedule] = useCreatingScheduleFromUrl();
  const [importing, setImporting] = useState(false);
  const [selectedLibrary, setSelectedLibrary] = useState<string>("");
  const [search, setSearch] = useState<string>("");
  const [reviewing, setReviewing] = useState<RuleSuggestion | null>(null);

  // The "Custom" tab shows only operator-authored rules. The
  // primary list query returns the union; we narrow here so the
  // count badge, the empty state, and the search filter all agree
  // about what "Custom" means.
  //
  // Stage 29: previously this filter only sorted by ``search``
  // text. Now it also excludes ``is_builtin`` rows.
  const customRules = useMemo(
    () => (rules.data ?? []).filter((r) => !r.is_builtin),
    [rules.data],
  );

  const visibleRules = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return customRules;
    return customRules.filter(
      (r) =>
        r.name.toLowerCase().includes(q) ||
        (r.description ?? "").toLowerCase().includes(q),
    );
  }, [customRules, search]);

  const pendingSuggestionsCount = (suggestions.data ?? []).length;

  async function onDuplicate(rule: Rule) {
    try {
      const copy = await duplicate.mutateAsync(rule.id);
      toast(`Duplicated as ${copy.name}`, "ok");
    } catch (err) {
      toast(
        `Could not duplicate ${rule.name}: ${
          err instanceof Error ? err.message : String(err)
        }`,
        "error",
        5000,
      );
    }
  }

  function onToggle(rule: Rule) {
    update.mutate({ id: rule.id, patch: { enabled: !rule.enabled } });
  }

  function onDelete(rule: Rule) {
    if (confirm(`Delete rule "${rule.name}"?`)) {
      remove.mutate(rule.id);
    }
  }

  return {
    rules,
    builtinRules,
    suggestions,
    libraries,
    remove,
    update,
    duplicate,
    evaluate,
    tab,
    setTab,
    search,
    setSearch,
    selectedLibrary,
    setSelectedLibrary,
    importing,
    setImporting,
    reviewing,
    setReviewing,
    creatingSchedule,
    setCreatingSchedule,
    jobKindsLoading: jobKinds.isLoading,
    customRules,
    visibleRules,
    pendingSuggestionsCount,
    onDuplicate,
    onToggle,
    onDelete,
  };
}

// Stage 10 audit fix (Issue 15): the tab is derived from the
// URL ``?tab=`` query param so /rules?tab=automation lands
// directly on the Automation tab — that's what the /automation
// redirect relies on, and bookmarks/share links benefit too.
// Reading from URL also means refresh stays on the current tab
// without needing a separate persistence store.
const VALID_TABS: ReadonlySet<RulesTab> = new Set<RulesTab>([
  "custom",
  "builtin",
  "suggestions",
  "automation",
]);

function isValidTab(value: string | null): value is RulesTab {
  return value !== null && (VALID_TABS as ReadonlySet<string>).has(value);
}

function useTabFromUrl(): [RulesTab, (next: RulesTab) => void] {
  const [searchParams, setSearchParams] = useSearchParams();
  const raw = searchParams.get("tab");
  const tab: RulesTab = isValidTab(raw) ? raw : "custom";
  function setTab(next: RulesTab) {
    // ``replace`` so back-button doesn't accumulate one history
    // entry per tab click — that's noise the user doesn't want.
    const params = new URLSearchParams(searchParams);
    params.set("tab", next);
    setSearchParams(params, { replace: true });
  }
  return [tab, setTab];
}

// Stage 2: ``?new=schedule`` opens the New-schedule dialog on the
// Automation tab. URL-driven so the page header (which lives outside
// AutomationTabContent) and the body component can share the
// open/close signal without prop-drilling or a shared context. The
// dialog is closed by clearing the param, which also leaves a clean
// URL for sharing.
function useCreatingScheduleFromUrl(): [boolean, (v: boolean) => void] {
  const [searchParams, setSearchParams] = useSearchParams();
  const open = searchParams.get("new") === "schedule";
  function setOpen(next: boolean) {
    const params = new URLSearchParams(searchParams);
    if (next) {
      params.set("new", "schedule");
    } else {
      params.delete("new");
    }
    setSearchParams(params, { replace: true });
  }
  return [open, setOpen];
}
