/**
 * Stage 8 (audit follow-up) — useScanProgress + ScanProgressBar.
 *
 * Pins:
 *   - useScanProgress handles ``scan.progress`` events and computes
 *     a 0..99 percent (caps at 99 until ``scan.completed``).
 *   - ``scan.completed`` snaps percent to 100 and flips
 *     ``recentlyCompleted`` true for 5s.
 *   - ``scan.started`` resets to seen=0, total=null, percent=null.
 *   - ScanProgressBar renders three states: hidden, indeterminate,
 *     determinate.
 *
 * We drive useScanProgress via a stub useWebSocketEvents (the real
 * hook reads from a singleton WS client we don't want to wire up
 * in unit tests).
 */

import { act, renderHook, render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import type { ReactNode } from "react";

// Stub: useWebSocketEvents calls the callback we supply at mount
// time. We capture the callback so tests can fire synthetic events.
let capturedHandler: ((event: { name: string; payload: unknown }) => void) | null =
  null;
vi.mock("@/hooks/useWebSocketEvents", () => ({
  useWebSocketEvents: (
    handler: (event: { name: string; payload: unknown }) => void,
  ) => {
    capturedHandler = handler;
  },
}));

// Avoid invalidation noise — useScanProgress calls invalidateRelated
// which would otherwise interact with our QueryClient.
vi.mock("@/lib/invalidate", () => ({
  invalidateRelated: vi.fn(),
}));

import { useScanProgress, useScanProgressWs } from "@/hooks/useScanProgress";
import { useScanProgressStore } from "@/stores/scanProgressStore";
import { ScanProgressBar } from "@/components/ui/ScanProgressBar";

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
  // Stage 13 — the store is module-level; reset its state
  // between tests so leftovers don't leak.
  useScanProgressStore.getState().reset();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

/**
 * Stage 13 — mount both hooks. ``useScanProgressWs`` wires
 * the WS callback so ``fire()`` works; ``useScanProgress``
 * returns the snapshot the test asserts against.
 */
function useScanProgressForTest() {
  useScanProgressWs();
  return useScanProgress();
}

describe("Stage 8 — useScanProgress", () => {
  it("starts at idle state (no runId, null percent)", () => {
    const { result } = renderHook(() => useScanProgressForTest(), { wrapper: Wrapper });
    expect(result.current.runId).toBeNull();
    expect(result.current.percent).toBeNull();
    expect(result.current.filesTotalEstimate).toBeNull();
  });

  it("scan.started resets to seen=0 with no total estimate yet", () => {
    const { result } = renderHook(() => useScanProgressForTest(), { wrapper: Wrapper });
    act(() => {
      fire("scan.started", { run_id: "r1", library_id: "lib-1" });
    });
    expect(result.current.runId).toBe("r1");
    expect(result.current.libraryId).toBe("lib-1");
    expect(result.current.filesSeen).toBe(0);
    expect(result.current.filesTotalEstimate).toBeNull();
    expect(result.current.percent).toBeNull();
  });

  it("scan.progress with total estimate computes a percent", () => {
    const { result } = renderHook(() => useScanProgressForTest(), { wrapper: Wrapper });
    act(() => {
      fire("scan.started", { run_id: "r1", library_id: "lib-1" });
      fire("scan.progress", {
        run_id: "r1",
        library_id: "lib-1",
        files_seen: 50,
        files_total_estimate: 200,
      });
    });
    // 50 / 200 = 25%.
    expect(result.current.filesSeen).toBe(50);
    expect(result.current.filesTotalEstimate).toBe(200);
    expect(result.current.percent).toBe(25);
  });

  it("caps percent at 99 even when seen approaches total", () => {
    // The scanner can briefly report files_seen close to or equal to
    // files_total_estimate before the completed event arrives.
    // Hitting 100% before completion would deceive the operator —
    // the bar should plateau at 99% until the run is finalized.
    const { result } = renderHook(() => useScanProgressForTest(), { wrapper: Wrapper });
    act(() => {
      fire("scan.started", { run_id: "r1", library_id: "lib-1" });
      fire("scan.progress", {
        run_id: "r1",
        library_id: "lib-1",
        files_seen: 200,
        files_total_estimate: 200,
      });
    });
    expect(result.current.percent).toBe(99);
  });

  it("scan.completed snaps percent to 100 and flips recentlyCompleted", () => {
    const { result } = renderHook(() => useScanProgressForTest(), { wrapper: Wrapper });
    act(() => {
      fire("scan.started", { run_id: "r1", library_id: "lib-1" });
      fire("scan.completed", {
        run_id: "r1",
        library_id: "lib-1",
        files_seen: 1234,
      });
    });
    expect(result.current.percent).toBe(100);
    expect(result.current.filesSeen).toBe(1234);
    expect(result.current.recentlyCompleted).toBe(true);
  });

  it("recentlyCompleted fades after 5 seconds", () => {
    const { result } = renderHook(() => useScanProgressForTest(), { wrapper: Wrapper });
    act(() => {
      fire("scan.completed", {
        run_id: "r1",
        library_id: "lib-1",
        files_seen: 1234,
      });
    });
    expect(result.current.recentlyCompleted).toBe(true);
    act(() => {
      vi.advanceTimersByTime(5_100);
    });
    expect(result.current.recentlyCompleted).toBe(false);
    // After the fade the percent and total should also reset so the
    // next mount starts clean — the bar shouldn't linger at 100%.
    expect(result.current.percent).toBeNull();
    expect(result.current.filesTotalEstimate).toBeNull();
  });

  it("scan.failed resets the entire progress state", () => {
    const { result } = renderHook(() => useScanProgressForTest(), { wrapper: Wrapper });
    act(() => {
      fire("scan.started", { run_id: "r1", library_id: "lib-1" });
      fire("scan.progress", {
        run_id: "r1",
        library_id: "lib-1",
        files_seen: 50,
        files_total_estimate: 200,
      });
      fire("scan.failed", { run_id: "r1", library_id: "lib-1" });
    });
    expect(result.current.runId).toBeNull();
    expect(result.current.filesSeen).toBe(0);
    expect(result.current.percent).toBeNull();
  });

  it("progress without total estimate stays indeterminate (percent null)", () => {
    // The scanner emits an initial scan.progress with files_seen=0
    // and a total estimate, but a misconfigured or older scanner
    // might not include the total. Make sure we don't crash.
    const { result } = renderHook(() => useScanProgressForTest(), { wrapper: Wrapper });
    act(() => {
      fire("scan.started", { run_id: "r1", library_id: "lib-1" });
      fire("scan.progress", {
        run_id: "r1",
        library_id: "lib-1",
        files_seen: 42,
        // no files_total_estimate
      });
    });
    expect(result.current.filesSeen).toBe(42);
    expect(result.current.percent).toBeNull();
  });
});

describe("Stage 8 — ScanProgressBar", () => {
  // Stage 13 — the bar reads state from the store; tests need
  // the WS subscription mounted so ``fire()`` reaches the store.
  // A small wrapper renders both the bar and an invisible
  // ``useScanProgressWs`` consumer.
  function BarTestHarness() {
    useScanProgressWs();
    return <ScanProgressBar />;
  }

  it("renders nothing when idle (no run + no recent completion)", () => {
    const { container } = render(<Wrapper><BarTestHarness /></Wrapper>);
    // Component returns null in idle state.
    expect(container.firstChild).toBeNull();
  });

  it("renders an indeterminate state while the scanner is enumerating", () => {
    const { container } = render(<Wrapper><BarTestHarness /></Wrapper>);
    act(() => {
      fire("scan.started", { run_id: "r1", library_id: "lib-1" });
    });
    const bar = container.querySelector('[data-progress="active"]');
    expect(bar).not.toBeNull();
    // No determinate percent yet.
    expect(bar!.getAttribute("data-percent")).toBe("-");
  });

  it("renders a determinate state once progress events arrive", () => {
    const { container } = render(<Wrapper><BarTestHarness /></Wrapper>);
    act(() => {
      fire("scan.started", { run_id: "r1", library_id: "lib-1" });
      fire("scan.progress", {
        run_id: "r1",
        library_id: "lib-1",
        files_seen: 100,
        files_total_estimate: 200,
      });
    });
    const bar = container.querySelector('[data-progress="active"]');
    expect(bar).not.toBeNull();
    expect(bar!.getAttribute("data-percent")).toBe("50");
  });

  it("shows the file count when total is known", () => {
    const { getByText } = render(<Wrapper><BarTestHarness /></Wrapper>);
    act(() => {
      fire("scan.started", { run_id: "r1", library_id: "lib-1" });
      fire("scan.progress", {
        run_id: "r1",
        library_id: "lib-1",
        files_seen: 50,
        files_total_estimate: 200,
      });
    });
    expect(getByText("50 / 200")).toBeInTheDocument();
  });

  it("shows 'Scan complete' label briefly after completion", () => {
    const { getByText } = render(<Wrapper><BarTestHarness /></Wrapper>);
    act(() => {
      fire("scan.completed", {
        run_id: "r1",
        library_id: "lib-1",
        files_seen: 1234,
      });
    });
    expect(getByText(/scan complete/i)).toBeInTheDocument();
  });
});
