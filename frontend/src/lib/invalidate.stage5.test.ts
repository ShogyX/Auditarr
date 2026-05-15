/**
 * Stage 5 (audit follow-up) — invalidation graph + deferred helper.
 *
 * Pins:
 *   - ``invalidateRelated`` calls ``invalidateQueries`` once per
 *     graph entry with no refetchType (default behaviour: eager).
 *   - ``invalidateRelatedDeferred`` calls ``invalidateQueries`` with
 *     ``refetchType: "none"`` so the synchronous freeze on heavy
 *     deletes (audit issue L6) is gone.
 */

import { describe, expect, it, vi } from "vitest";

import {
  invalidateRelated,
  invalidateRelatedDeferred,
} from "@/lib/invalidate";

function fakeQc() {
  const calls: { queryKey: unknown[]; refetchType?: string }[] = [];
  const qc = {
    invalidateQueries: vi.fn(
      (opts: { queryKey: unknown[]; refetchType?: string }) => {
        calls.push(opts);
      },
    ),
  } as unknown as Parameters<typeof invalidateRelated>[0];
  return { qc, calls };
}

describe("Stage 5 — invalidation helpers", () => {
  it("invalidateRelated fires eager refetches for every library-graph key", () => {
    const { qc, calls } = fakeQc();
    invalidateRelated(qc, "library");

    // Library graph: 8 prefixes — libraries, dashboard, scans,
    // scan-progress, media, files, notifications, rules.
    expect(calls).toHaveLength(8);
    for (const c of calls) {
      // No refetchType means React Query uses its default
      // ("active") which triggers refetches.
      expect(c.refetchType).toBeUndefined();
    }
  });

  it("invalidateRelatedDeferred uses refetchType=none for every key", () => {
    const { qc, calls } = fakeQc();
    invalidateRelatedDeferred(qc, "library");

    expect(calls).toHaveLength(8);
    for (const c of calls) {
      expect(c.refetchType).toBe("none");
    }
  });

  it("both helpers cover the same key set (deferred just changes refetchType)", () => {
    const { qc: eagerQc, calls: eagerCalls } = fakeQc();
    const { qc: deferredQc, calls: deferredCalls } = fakeQc();
    invalidateRelated(eagerQc, "library");
    invalidateRelatedDeferred(deferredQc, "library");

    const eagerKeys = eagerCalls.map((c) => c.queryKey[0]).sort();
    const deferredKeys = deferredCalls.map((c) => c.queryKey[0]).sort();
    expect(deferredKeys).toEqual(eagerKeys);
  });
});
