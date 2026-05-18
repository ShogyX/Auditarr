import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { invalidateRelated, invalidateMany } from "@/lib/invalidate";
import { apiClient } from "@/services/apiClient";

// ── Types ─────────────────────────────────────────────────────
export interface Rule {
  id: string;
  name: string;
  description: string | null;
  enabled: boolean;
  priority: number;
  definition: RuleDefinition;
  // Stage 29: True for rules seeded by the codebase. Read-only at
  // the API layer for name / description / definition; only
  // ``enabled`` and ``priority`` can be patched. Defaults to false
  // for forward compatibility with bundles or older clients that
  // don't carry the field.
  is_builtin?: boolean;
  last_evaluated_at: string | null;
  last_match_count: number;
  created_at: string;
  updated_at: string;
}

export interface RuleDefinition {
  match: Match;
  actions: Action[];
  /**
   * Stage 06 (v1.7) — destructive-action acknowledgement.
   *
   * Per addendum A.0.1: a rule that contains any ``delete`` action
   * MUST carry ``acknowledged_destructive: true`` at the rule
   * level. The backend's ``RuleDefinition`` Pydantic model rejects
   * delete-action bodies without this flag (and forbids it on
   * non-delete bodies). The visual rule builder renders the flag
   * as a checkbox labelled "I understand this rule deletes files
   * from disk." driven by ``vocabulary.rule_flags``.
   *
   * Optional in the TS type so existing rule payloads (no delete
   * action) don't need to set it; the builder writes ``true``
   * only when needed and ``undefined`` otherwise. The backend
   * defaults absent to ``false``.
   */
  acknowledged_destructive?: boolean;
}

export type Match = Condition | AllOf | AnyOf;

export interface Condition {
  field: string;
  op: string;
  value: unknown;
}

export interface AllOf {
  all: Match[];
}

export interface AnyOf {
  any: Match[];
}

export type Action =
  | { type: "set_severity"; severity: string }
  | { type: "add_tag"; tag: string }
  | { type: "queue_optimization"; profile: string }
  | {
      type: "notify";
      channel: string;
      message?: string | null;
      /**
       * Stage 06 (v1.7) — optional throttle. The rules service
       * gates the dispatch through ``rule_notification_windows``;
       * matches beyond ``max_per_window`` in a rolling
       * ``window_seconds`` window are suppressed and a
       * ``rule.throttled`` event is emitted on the bus. The
       * backend enforces ``window_seconds >= 60`` and
       * ``max_per_window >= 1``.
       */
      throttle?: NotifyThrottle | null;
    }
  // Stage 9 (audit follow-up), updated Stage 05 (v1.7): Stage 05
  // retired the Quarantine action and Delete's ``confirm`` flag
  // (Section A.0 — "delete means delete"). Delete is now
  // unconditional; the optional ``reason`` lands in the
  // ``file.deleted`` audit-log entry the service emits on every
  // successful delete.
  | { type: "delete"; reason?: string | null }
  // v1.9 Stage 4.6 — VT lookup as a rule action. No params
  // today; the schema's ``extra="forbid"`` reserves the
  // namespace for future params.
  | { type: "vt_lookup" }
  // v1.9 Stage 5.1 — cross-integration search trigger. The
  // service layer reads {target, integration_id}, resolves the
  // integration row, and calls the provider's ``trigger_search``
  // method.
  | {
      type: "search_upstream";
      target: string;
      integration_id: string;
    };

/** Stage 06 (v1.7) — Notify throttle config. */
export interface NotifyThrottle {
  window_seconds: number;
  max_per_window: number;
}

export interface DryRunResult {
  matched: boolean;
  severity: string | null;
  severity_rank: number;
  add_tags: string[];
  queue_optimizations: string[];
}

export interface RuleCreatePayload {
  name: string;
  description?: string;
  enabled?: boolean;
  priority?: number;
  definition: RuleDefinition;
}

export interface RuleUpdatePayload {
  name?: string;
  description?: string;
  enabled?: boolean;
  priority?: number;
  definition?: RuleDefinition;
}

// ── Stage 15: rule vocabulary for the visual builder ─────────
export interface RuleVocabularyField {
  key: string;
  label: string;
  type: "numeric" | "string" | "bool" | "array";
  enum: string[] | null;
}

export interface RuleVocabularyAction {
  type: string;
  label: string;
  args_schema: Record<string, RuleVocabularyArgSchema>;
}

export interface RuleVocabularyArgSchema {
  type: string;
  enum?: string[];
  minLength?: number;
  maxLength?: number;
  /**
   * Stage 06 (v1.7) — nested object args. The Notify action's
   * ``throttle`` arg is the first object-typed arg in the
   * vocabulary; its sub-properties live here. ``minimum`` is
   * surfaced on numeric children so the builder can validate
   * client-side before posting.
   */
  properties?: Record<string, RuleVocabularyArgSchema>;
  minimum?: number;
  required?: boolean;
  hint?: string;
  /**
   * v1.9 Stage 5.1 — opt-in special renderer hint. The only
   * value defined today is ``"integration_picker"`` for the
   * ``search_upstream`` action's ``integration_id`` arg. The
   * frontend's ActionRow short-circuits to a dedicated renderer
   * (SearchUpstreamArgs) when ``action.type ===
   * "search_upstream"``; this field is informational so that
   * frontend code reading the vocabulary directly (e.g. tests,
   * future hand-rolled UIs) sees the marker. The default
   * renderer ignores it.
   */
  format?: string;
}

/**
 * Stage 06 (v1.7) — rule-level boolean flags the builder must
 * surface. Today carries only ``acknowledged_destructive``
 * (addendum A.0.1). Each entry advertises its label, hint, and
 * the conditional-visibility rule (``required_when``).
 */
export interface RuleVocabularyFlag {
  type: "bool";
  label: string;
  hint?: string;
  /**
   * The builder shows + enforces the flag only when this
   * predicate matches the rule's current shape. The only
   * predicate today is ``{ any_action_type: "delete" }`` —
   * meaning "show this flag when any action in the rule is a
   * delete action."
   */
  required_when?: { any_action_type?: string };
}

export interface RuleVocabulary {
  fields: RuleVocabularyField[];
  ops: Record<"numeric" | "string" | "bool" | "array", string[]>;
  severities: string[];
  actions: RuleVocabularyAction[];
  /** Stage 06 — rule-level flag definitions. Optional for back-
   * compat with older API versions that didn't return the field. */
  rule_flags?: Record<string, RuleVocabularyFlag>;
}

// ── Hooks ─────────────────────────────────────────────────────
export function useRuleVocabulary() {
  return useQuery({
    queryKey: ["rules", "vocabulary"],
    queryFn: () => apiClient.get<RuleVocabulary>("/rules/vocabulary"),
    // Vocabulary is essentially static — only changes when the backend
    // ships a new rule schema. Long stale time and no refetch on focus.
    staleTime: 60 * 60 * 1000,
  });
}

export function useRules(filters?: { is_builtin?: boolean }) {
  return useQuery({
    queryKey: ["rules", "list", filters ?? {}],
    queryFn: () => {
      // Stage 29: the optional ``is_builtin`` filter becomes a
      // backend query parameter. When omitted, the API returns
      // both custom and builtin rules — that's what the default
      // "Custom" tab and the search-across-everything code path
      // expect today. The dedicated "Built-in" tab passes
      // ``is_builtin: true``; nobody currently passes ``false``
      // because the "Custom" tab still uses the union and
      // filters client-side, preserving prior behavior.
      const qs = new URLSearchParams();
      if (filters?.is_builtin !== undefined) {
        qs.set("is_builtin", filters.is_builtin ? "true" : "false");
      }
      const suffix = qs.toString();
      return apiClient.get<Rule[]>(`/rules${suffix ? `?${suffix}` : ""}`);
    },
    staleTime: 15_000,
  });
}

// Stage 30: single-rule fetch for the routed editor at
// /rules/:ruleId/edit. The list query (``useRules``) is still
// the right primary cache; this gives the editor a direct path
// to a fresh row by id without the operator having to bounce
// through the list. Disabling when id is null lets the
// editor handle the "new rule" branch without an extra query.
export function useRule(id: string | null | undefined) {
  return useQuery({
    queryKey: ["rules", "detail", id],
    queryFn: () => apiClient.get<Rule>(`/rules/${id}`),
    enabled: !!id,
    staleTime: 15_000,
  });
}

export function useCreateRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RuleCreatePayload) => apiClient.post<Rule>("/rules", body),
    onSuccess: () => invalidateRelated(qc, "rule"),
  });
}

export function useUpdateRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: RuleUpdatePayload }) =>
      apiClient.patch<Rule>(`/rules/${id}`, patch),
    onSuccess: () => invalidateRelated(qc, "rule"),
  });
}

export function useDeleteRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.delete(`/rules/${id}`),
    onSuccess: () => invalidateRelated(qc, "rule"),
  });
}

export function useDryRunRule() {
  return useMutation({
    mutationFn: (body: { definition: RuleDefinition; media_file_id: string }) =>
      apiClient.post<DryRunResult>("/rules/dry-run", body),
  });
}

export function useEvaluateLibrary() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (libraryId: string) =>
      apiClient.post<{ library_id: string; files_evaluated: number }>(
        `/rules/libraries/${libraryId}/evaluate`,
        {},
      ),
    // Library-scoped re-evaluation changes both rule outcomes and
    // the media files that absorb the new severities.
    onSuccess: () => invalidateMany(qc, ["rule", "media"]),
  });
}

// v1.9 OP-15 — targeted single-rule re-evaluation.
// Fires the named rule against every file in every library.
// Used by the rule editor's "Save & Evaluate" affordance: after
// the rule saves successfully, the operator clicks once to fire
// it against existing media without running every other rule too.
export function useEvaluateRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ruleId: string) =>
      apiClient.post<{ rule_id: string; files_evaluated: number }>(
        `/rules/${ruleId}/evaluate-now`,
        {},
      ),
    // Same invalidation surface as evaluate_library: rule
    // outcomes change AND media files absorb new severities/tags.
    onSuccess: () => invalidateMany(qc, ["rule", "media"]),
  });
}

// ── Stage 16: rule suggestion types ───────────────────────────
export interface RuleSuggestion {
  id: string;
  name: string;
  definition: RuleDefinition;
  heuristic: string;
  evidence: Record<string, unknown>;
  files_affected: number;
  est_runtime_s: number | null;
  confidence: number;
  dedup_key: string;
  status: "pending" | "deployed" | "dismissed";
  deployed_rule_id: string | null;
  deployed_at: string | null;
  dismissed_at: string | null;
  dismissed_reason: string | null;
  created_at: string;
}

export interface SuggestionDeployPayload {
  name?: string;
  description?: string;
  priority?: number;
  enabled?: boolean;
  definition?: RuleDefinition;
}

export interface AnalyzePlaybackOutcome {
  examined_events: number;
  candidates_generated: number;
  suggestions_created: number;
  skipped_deduped: number;
  skipped_dismissed: number;
  skipped_deployed: number;
  skipped_too_few_events: boolean;
  // ── Stage 09 (v1.7) — playback-count fix ───────────────────
  // The recommendation card reads ``examined_events_total``
  // (not ``examined_events``, which is resolved-only) so the
  // operator-visible count is the *true* number of playback
  // events. When ``examined_events_unresolved > 0`` the card
  // renders a path-mappings hint per addendum A.7.
  // Defaulted to 0 in the runtime path so older backends
  // without these fields still parse.
  examined_events_total?: number;
  examined_events_resolved?: number;
  examined_events_unresolved?: number;
}

// ── Stage 16: hooks ───────────────────────────────────────────
export function useRuleSuggestions() {
  return useQuery({
    queryKey: ["rules", "suggestions"],
    queryFn: () => apiClient.get<RuleSuggestion[]>("/rules/suggestions"),
    // Suggestions only change on the daily cron tick or manual deploy/dismiss.
    // Re-fetch every minute is plenty for the dashboard card.
    staleTime: 60_000,
  });
}

export function useRuleSuggestion(id: string | null) {
  return useQuery({
    queryKey: ["rules", "suggestions", id],
    queryFn: () => apiClient.get<RuleSuggestion>(`/rules/suggestions/${id}`),
    enabled: id !== null,
  });
}

export function useDeploySuggestion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: SuggestionDeployPayload }) =>
      apiClient.post<RuleSuggestion>(`/rules/suggestions/${id}/deploy`, patch),
    // Deploy creates a rule AND moves a suggestion to "deployed",
    // so refresh both rules and the suggestion namespace.
    onSuccess: () => invalidateMany(qc, ["rule", "rule-suggestion"]),
  });
}

export function useDismissSuggestion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) =>
      apiClient.post<RuleSuggestion>(`/rules/suggestions/${id}/dismiss`, {
        reason: reason ?? null,
      }),
    onSuccess: () => invalidateRelated(qc, "rule-suggestion"),
  });
}

export function useRunAnalyzer() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.post<AnalyzePlaybackOutcome>("/rules/analyze-playback/run", {}),
    onSuccess: () => invalidateRelated(qc, "rule-suggestion"),
  });
}

// ── Stage 24: duplicate / export / import ──────────────────────

/** Duplicate an existing rule. Returns the new (disabled) copy.
 *  The backend handles name-collision resolution server-side — the
 *  UI just submits a request and renders whatever name comes back. */
export function useDuplicateRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ruleId: string) =>
      apiClient.post<Rule>(`/rules/${encodeURIComponent(ruleId)}/duplicate`),
    onSuccess: () => invalidateRelated(qc, "rule"),
  });
}

export interface RuleExportEntry {
  name: string;
  description: string | null;
  enabled: boolean;
  priority: number;
  definition: RuleDefinition;
}

export interface RuleExportBundle {
  version: string;
  exported_at: string;
  rules: RuleExportEntry[];
}

export type ImportConflictStrategy = "skip" | "rename" | "overwrite";

export interface RuleImportOutcome {
  name: string;
  final_name: string;
  action: "created" | "skipped" | "renamed" | "overwritten" | "error";
  rule_id: string | null;
  error: string | null;
}

export interface RuleImportResponse {
  created: number;
  skipped: number;
  renamed: number;
  overwritten: number;
  errors: number;
  outcomes: RuleImportOutcome[];
}

/** Fetch the export bundle. Lazy-fetch (``enabled: false`` by default)
 *  because the operator triggers export explicitly via a button — the
 *  bundle isn't otherwise consumed by the UI. */
export function useExportRules(opts?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["rules", "export"] as const,
    queryFn: () => apiClient.get<RuleExportBundle>("/rules/bundle/export"),
    enabled: opts?.enabled ?? false,
    refetchOnWindowFocus: false,
    staleTime: 0,
    // We disable retries here because export is a one-shot user
    // action; if it fails, the operator can re-click. Auto-retrying
    // a 5xx would be confusing in this flow.
    retry: false,
  });
}

/** Import a rule bundle. Invalidates the rules list on success so the
 *  newly-imported rows show up immediately. */
export function useImportRules() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      bundle,
      onConflict,
    }: {
      bundle: RuleExportBundle;
      onConflict: ImportConflictStrategy;
    }) =>
      apiClient.post<RuleImportResponse>("/rules/bundle/import", {
        bundle,
        on_conflict: onConflict,
      }),
    onSuccess: () => invalidateRelated(qc, "rule"),
  });
}

// ── Stage 14b (audit follow-up): matched-files tab ────────────

/** One file matched by a rule, joined to its media row. */
export interface RuleMatchedFile {
  media_file_id: string;
  library_id: string;
  path: string;
  filename: string;
  severity: string;
  severity_rank: number;
  evaluated_at: string;
}

/** Fetch the list of files matched by a rule. Used by the Rule
 *  editor's new "Matched files" tab. */
export function useRuleMatchedFiles(
  ruleId: string | null,
  limit = 200,
) {
  return useQuery({
    queryKey: ["rules", "matched-files", ruleId, limit] as const,
    queryFn: () =>
      apiClient.get<RuleMatchedFile[]>(
        `/rules/${encodeURIComponent(ruleId!)}/matched-files?limit=${limit}`,
      ),
    enabled: !!ruleId,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
}


// ── v1.9 Stage 4.4 — Rule templates ──────────────────────────

export interface RuleTemplate {
  id: string;
  name: string;
  description: string | null;
  priority: number;
  definition: RuleDefinition;
  seeded_at: string;
  created_at: string;
  updated_at: string;
}

/** List all rule templates. */
export function useRuleTemplates() {
  return useQuery({
    queryKey: ["rule-templates"] as const,
    queryFn: () => apiClient.get<RuleTemplate[]>("/rule-templates"),
    staleTime: 60_000,
  });
}

/** "Use template" mutation — creates a new operator-owned Rule
 *  from the named template. The backend handles name-collision
 *  resolution (appends " (copy)" / " (copy 2)") so the UI just
 *  submits and renders whatever name comes back. */
export function useUseRuleTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (templateId: string) =>
      apiClient.post<Rule>(
        `/rule-templates/${encodeURIComponent(templateId)}/use`,
      ),
    onSuccess: () => invalidateRelated(qc, "rule"),
  });
}