/**
 * Stage 13 (plan §611) — useScanProgress survives mount/
 * unmount/remount cycles.
 *
 * Pre-Stage-13 the scan-progress state lived in the
 * useScanProgress hook's own ``useState``, and the hook's
 * cleanup effect reset to ``INITIAL`` on unmount. Operators
 * starting a scan from /dashboard, then navigating to
 * /files, would see the progress bar disappear and re-mount
 * from zero on return.
 *
 * Stage 13 moved state into ``scanProgressStore`` and split
 * the WS subscription off into ``useScanProgressWs``. The
 * subscription lives at the AppShell so it doesn't unmount
 * with route changes; the store retains state across
 * any consumer mount/unmount cycle.
 *
 * This test pins: start a scan via the store, unmount the
 * consumer, remount, assert progress survives.
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Stub the WS hook so the test doesn't pull in the real
// WS client (which expects a backend connection).
let capturedHandler: ((event: { name: string; payload: unknown }) => void) | null =
  null;
vi.mock("@/hooks/useWebSocketEvents", () => ({
  useWebSocketEvents: (
    handler: (event: { name: string; payload: unknown }) => void,
  ) => {
    capturedHandler = handler;
  },
}));

vi.mock("@/lib/invalidate", () => ({
  invalidateRelated: vi.fn(),
}));

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useScanProgress, useScanProgressWs } from "@/hooks/useScanProgress";
import { useScanProgressStore } from "@/stores/scanProgressStore";
import type { ReactNode } from "react";

function Wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

function fire(name: string, payload: unknown): void {
  capturedHandler!({ name, payload });
}

beforeEach(() => {
  capturedHandler = null;
  useScanProgressStore.getState().reset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Stage 13 — useScanProgress persists across mount/unmount", () => {
  it("the progress state survives a consumer unmount + remount", () => {
    // Mount the subscriber + a consumer.
    const { result: r1, unmount: u1 } = renderHook(
      () => {
        useScanProgressWs();
        return useScanProgress();
      },
      { wrapper: Wrapper },
    );

    // Fire scan events to populate the store.
    act(() => {
      fire("scan.started", { run_id: "r1", library_id: "lib-1" });
      fire("scan.progress", {
        run_id: "r1",
        library_id: "lib-1",
        files_seen: 100,
        files_total_estimate: 200,
      });
    });

    expect(r1.current.runId).toBe("r1");
    expect(r1.current.filesSeen).toBe(100);
    expect(r1.current.percent).toBe(50);

    // Unmount the consumer entirely — pre-Stage-13 this
    // would have called ``setProgress(INITIAL)`` via the
    // cleanup effect and lost the state. After Stage 13
    // there's no per-component cleanup; state lives in
    // the central store.
    u1();

    // Remount with a fresh consumer; we should still see
    // the same snapshot. We DO mount the WS hook again to
    // mirror what the AppShell does — in real app code the
    // WS subscription stays mounted at shell level, but
    // the assertion is the SAME: state survives.
    const { result: r2 } = renderHook(
      () => {
        useScanProgressWs();
        return useScanProgress();
      },
      { wrapper: Wrapper },
    );

    expect(r2.current.runId).toBe("r1");
    expect(r2.current.libraryId).toBe("lib-1");
    expect(r2.current.filesSeen).toBe(100);
    expect(r2.current.percent).toBe(50);
  });

  it("a remount without the WS subscription still reads the persisted store state", () => {
    /* This is the plan §616 contract: the user is on the
       dashboard (WS subscriber mounted at shell), starts a
       scan, navigates to /files (no scan badge there =
       no consumer mounted), then returns — the consumer
       re-mounts and immediately sees the live state.
       The shell-level WS subscription was never unmounted. */
    const { unmount: unmountFirst } = renderHook(
      () => {
        useScanProgressWs();
        return useScanProgress();
      },
      { wrapper: Wrapper },
    );

    act(() => {
      fire("scan.started", { run_id: "r1", library_id: "lib-1" });
      fire("scan.progress", {
        run_id: "r1",
        library_id: "lib-1",
        files_seen: 75,
        files_total_estimate: 300,
      });
    });

    // Don't unmount the WS hook — it stays at shell level
    // in real usage. Just remount a NEW consumer.
    const { result } = renderHook(() => useScanProgress(), {
      wrapper: Wrapper,
    });

    expect(result.current.runId).toBe("r1");
    expect(result.current.filesSeen).toBe(75);
    expect(result.current.percent).toBe(25);

    unmountFirst();
  });

  it("the store outlives the route-level consumer (multiple consumers see the same state)", () => {
    // Two simultaneous consumers — both see the same store.
    const { result: r1 } = renderHook(() => useScanProgress(), {
      wrapper: Wrapper,
    });
    const { result: r2 } = renderHook(
      () => {
        useScanProgressWs();
        return useScanProgress();
      },
      { wrapper: Wrapper },
    );

    act(() => {
      fire("scan.started", { run_id: "rX", library_id: "libX" });
    });

    expect(r1.current.runId).toBe("rX");
    expect(r2.current.runId).toBe("rX");
  });
});
