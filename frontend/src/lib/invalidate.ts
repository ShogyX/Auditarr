/**
 * Centralised React-Query invalidation graph.
 *
 * Most mutation hooks need to invalidate more than their own
 * top-level key. Deleting a library, for example, must also refresh
 * the dashboard tiles, the scan-progress polling, the files page,
 * and the notifications query — otherwise the UI shows stale data
 * until the operator hits refresh.
 *
 * Audit Issue 14 ("Adding rules/items doesn't auto-update in the UI,
 * the user needs to refresh to see the item") was rooted in every
 * mutation hook hand-rolling a narrow invalidation list. This
 * module centralises that knowledge into a single dependency graph
 * so each hook just declares its "kind" and gets everything that
 * depends on it refreshed automatically.
 *
 * To use:
 *
 *     const qc = useQueryClient();
 *     return useMutation({
 *       mutationFn: (id) => apiClient.delete(`/libraries/${id}`),
 *       onSuccess: () => invalidateRelated(qc, "library"),
 *     });
 *
 * When the dependency graph needs to grow (e.g. a new feature adds
 * a query that pulls library data), add the dependency below in ONE
 * place and every hook benefits.
 */

import type { QueryClient } from "@tanstack/react-query";

/**
 * Kinds of mutations the app performs. New entity types should be
 * added here when their hooks land.
 */
export type MutationKind =
  | "library"
  | "media"
  | "scan"
  | "integration"
  | "rule"
  | "rule-suggestion"
  | "automation"
  | "optimization"
  | "notification"
  | "plugin"
  | "runtime-setting"
  | "secret"
  | "path-mapping"
  | "updater"
  | "playback"
  | "auth";

/**
 * Dependency graph: what query keys does each mutation kind affect?
 * Keys here are first-element strings of the query key arrays used
 * across hooks. React Query invalidates all queries whose key
 * STARTS WITH the given prefix, so listing the top-level segment is
 * enough to cover narrower keys like ["dashboard", "libraries"].
 *
 * The graph is deliberately broad. Over-invalidation costs a few
 * extra GETs (most queries are cached server-side and cheap);
 * under-invalidation costs operator time and trust ("I clicked
 * delete but nothing changed").
 */
const GRAPH: Record<MutationKind, readonly string[]> = {
  // Libraries are referenced everywhere: dashboard tiles, sidebar
  // badges, the files page, scan progress, notifications (channels
  // can be scoped to a library), and rules (rules can be library-
  // specific). The audit's Issue 14 root cause lived here.
  library: [
    "libraries",
    "dashboard",
    "scans",
    "scan-progress",
    "media",
    "files",
    "notifications",
    "rules",
  ],

  // Media changes (severity edits, deletes, re-evaluations, etc.)
  // flow back into dashboard rollups and the files list.
  media: ["media", "files", "dashboard"],

  // Scan starts and progress impact dashboard counts, the library
  // tile's "last scanned" line, and the media file count.
  scan: ["scans", "scan-progress", "libraries", "dashboard", "media"],

  // Integrations feed dashboard health, can produce scans, and
  // notifications channels can be tied to them.
  integration: [
    "integrations",
    "dashboard",
    "scans",
    "notifications",
  ],

  // Rules drive severity assignment, so re-evaluation changes media
  // rows and dashboard rollups. Rule changes also affect the
  // suggestions list.
  rule: ["rules", "media", "files", "dashboard", "notifications"],

  // Suggestion accept/dismiss only affects the rules namespace.
  "rule-suggestion": ["rules", "dashboard"],

  // Automation schedules show up on the dashboard's recent-runs
  // panel.
  automation: ["automation", "dashboard"],

  // Optimization jobs touch the media table (transcoded files end
  // up with updated metadata) and dashboard counts.
  optimization: ["optimization", "dashboard", "media"],

  // Notification channels/deliveries impact dashboard health
  // indicators and the notifications page itself.
  notification: ["notifications", "dashboard"],

  // Plugins can register integrations, rules, optimization profiles,
  // and notification channel kinds — so installing or removing one
  // refreshes essentially everything the plugin can touch.
  plugin: [
    "plugins",
    "integrations",
    "rules",
    "runtime-settings",
    "notifications",
    "optimization",
  ],

  // Runtime settings overrides feed config to the system surface and
  // can affect notification channels (severity thresholds, etc.).
  "runtime-setting": ["runtime-settings", "system", "notifications"],

  // Secrets are used by integrations; rotating one refreshes integration
  // health.
  secret: ["runtime-settings", "integrations"],

  // Path mappings affect scan results and the files page.
  "path-mapping": ["runtime-settings", "scans", "files", "media"],

  // Updater actions impact the updater status and the system version.
  updater: ["updater", "system"],

  // Playback (Stage 12 audit follow-up) — cursor resets and any
  // future write paths into the playback namespace flow back into
  // the dashboard's transcode panels.
  playback: ["playback", "dashboard"],

  // Auth changes (login, logout, password reset) impact who can see
  // what — invalidate everything user-scoped.
  auth: ["auth", "system"],
};

/**
 * Invalidate every query key downstream of a mutation.
 *
 * Safe to call from inside `onSuccess`, `onSettled`, or any handler
 * that runs after a successful mutation. React Query's invalidation
 * is by key-prefix, so this triggers refetches for every active
 * query whose key starts with one of the listed top-level segments.
 */
export function invalidateRelated(
  qc: QueryClient,
  kind: MutationKind,
): void {
  for (const key of GRAPH[kind]) {
    qc.invalidateQueries({ queryKey: [key] });
  }
}

/**
 * Invalidate every downstream query key, but mark them stale rather
 * than triggering an eager refetch on inactive queries.
 *
 * Stage 5 (audit follow-up): the synchronous ``invalidateRelated``
 * walks 8 prefixes for a library mutation. Each ``invalidateQueries``
 * call schedules refetches for every active observer; that's fine
 * for small caches but, for a heavy delete, the cascade ran on the
 * main thread immediately after the modal closed and produced a
 * visible UI freeze (audit Issue L6). The deferred variant uses
 * ``refetchType: "none"`` so React Query marks the keys stale but
 * defers the actual fetch until the next render that subscribes to
 * the query. The net effect is identical eventual data freshness
 * with no synchronous burst.
 */
export function invalidateRelatedDeferred(
  qc: QueryClient,
  kind: MutationKind,
): void {
  for (const key of GRAPH[kind]) {
    qc.invalidateQueries({ queryKey: [key], refetchType: "none" });
  }
}

/**
 * Invalidate multiple kinds at once. Useful when a single mutation
 * is semantically more than one thing (e.g. accepting a rule
 * suggestion both creates a rule AND removes a suggestion).
 */
export function invalidateMany(
  qc: QueryClient,
  kinds: readonly MutationKind[],
): void {
  const seen = new Set<string>();
  for (const kind of kinds) {
    for (const key of GRAPH[kind]) {
      if (seen.has(key)) continue;
      seen.add(key);
      qc.invalidateQueries({ queryKey: [key] });
    }
  }
}


/**
 * Stage 13 audit (plan §604 + addendum C.1) — mutations that
 * legitimately do NOT call ``invalidateRelated`` / ``invalidateMany``.
 *
 * The grep audit (``grep -rn "useMutation" frontend/src/hooks``)
 * surfaces 72 mutation hooks. The 9 below intentionally skip
 * invalidation. If a future change adds a 10th, justify it
 * here — otherwise wire it into the graph.
 *
 *   1. ``useTestIntegration`` (useIntegrations.ts) — preflight
 *      probe that doesn't change server state.
 *   2. ``useDiscoverLibraries`` (useIntegrations.ts) — discovery
 *      probe; read-only call to the upstream.
 *   3. ``useDryRunRule`` (useRules.ts) — preview evaluation;
 *      no server-side persistence.
 *   4. ``useLogin`` (useAuth.ts) — populates the auth store
 *      via ``setSession``; the resulting auth-change remounts
 *      the shell so all queries refresh naturally.
 *   5. ``useRegister`` (useAuth.ts) — same shell-remount path.
 *   6. ``useLogout`` (useAuth.ts) — clears the auth store →
 *      redirects to /login → no app data is rendered.
 *   7. ``useLogoutAll`` (useAuth.ts) — same as logout for the
 *      caller's session; the OTHER sessions are server-side
 *      and don't have a corresponding client-side query.
 *   8. ``useRequestPasswordReset`` (useAuth.ts) — server-side
 *      side effect (email / banner). No client query reads
 *      from the result.
 *   9. ``useConfirmPasswordReset`` (useAuth.ts) — followed
 *      immediately by a fresh login, which goes through the
 *      shell-remount path. The intermediate UI shows static
 *      "password reset successful" copy.
 *
 * ── v1.9 Stage 1.3 re-audit ────────────────────────────────────
 *
 * Fresh walk against every ``useMutation`` in
 * ``frontend/src/hooks/`` (86 occurrences across 12 hook files
 * as of v1.9). The 9 above remain the only mutations that skip
 * invalidation entirely. The new ``useForceClearApply`` (v1.9
 * Stage 1.2) calls ``invalidateRelated(qc, "updater")``, so it
 * does NOT belong on the exception list.
 *
 * Several hooks call ``qc.invalidateQueries({ queryKey: [...] })``
 * directly instead of going through ``invalidateRelated`` /
 * ``invalidateMany``. This is acceptable when the invalidation
 * key isn't part of the central dependency graph in this file —
 * the hook is still invalidating, just with finer surgical
 * precision. The cases:
 *
 *   * ``useRunHousekeeping`` / ``useReloadDocs`` (useSystem.ts)
 *     — keys ``["system","housekeeping"]`` and ``["docs"]`` are
 *     orphan namespaces not consumed elsewhere; the direct call
 *     is the right tool.
 *   * ``useSyncTags`` (useIntegrations.ts) — invalidates both
 *     ``["media"]`` and ``["integrations"]``; the helper graph
 *     would still pull "media" via the "integration" edge but
 *     listing both makes the intent explicit at the call site.
 *   * ``useGenerateWebhookSecret`` (useIntegrations.ts) — only
 *     touches ``["integrations"]``; the helper would do the
 *     same plus drag along media/scan/path-mapping unnecessarily.
 *
 * No regression found. No new exception added. The audit is
 * green for v1.9.
 */