/**
 * v1.9 Stage 1.1 — ScanProgressBar dynamic updates.
 *
 * Pins: three synthetic ``scan.progress`` events drive visible
 * updates in the bar's data-percent attribute without remounting
 * the component. The instance identity of the bar's host element
 * is captured before the first event and asserted unchanged after
 * the third, so a regression that recreates the DOM node on every
 * progress tick would surface here.
 *
 * This is the v1.9 counterpart to ``useScanProgress.stage8.test.tsx``
 * which exercises the hook contract; this one is the visual-contract
 * test the plan calls out.
 */

import { render, act } from "@testing-library/react";
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

// Stub: capture the WS handler so tests can fire synthetic events.
let capturedHandler: ((event: { name: string; payload: unknown }) => void) | null =
  null;
vi.mock("@/hooks/useWebSocketEvents", () => ({
  useWebSocketEvents: (
    handler: (event: { name: string; payload: unknown }) => void,
  ) => {
    capturedHandler = handler;
  },
}));

// Same as the Stage 8 test — keep the invalidate noise off.
vi.mock("@/lib/invalidate", () => ({
  invalidateRelated: vi.fn(),
}));

import { ScanProgressBar } from "@/components/ui/ScanProgressBar";
import { useScanProgressWs } from "@/hooks/useScanProgress";
import { useScanProgressStore } from "@/stores/scanProgressStore";

function Wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

function BarHarness() {
  useScanProgressWs();
  return <ScanProgressBar />;
}

function fire(name: string, payload: unknown): void {
  capturedHandler!({ name, payload });
}

beforeEach(() => {
  capturedHandler = null;
  useScanProgressStore.getState().reset();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("v1.9 Stage 1.1 — ScanProgressBar dynamic updates", () => {
  it("updates data-percent across three synthetic events without remount", () => {
    const { container } = render(
      <Wrapper>
        <BarHarness />
      </Wrapper>,
    );

    // Fire the start event so the bar exits idle state.
    act(() => {
      fire("scan.started", { run_id: "r1", library_id: "lib-1" });
    });

    // Capture the host element identity *after* the bar materializes.
    // The bar is the first element with data-progress; the
    // identity-stable check uses that node across the next two ticks.
    const initialBar = container.querySelector('[data-progress="active"]');
    expect(initialBar).not.toBeNull();
    // Indeterminate state — no determinate percent yet.
    expect(initialBar!.getAttribute("data-percent")).toBe("-");

    // First progress tick: 25 / 100 → 25%.
    act(() => {
      fire("scan.progress", {
        run_id: "r1",
        library_id: "lib-1",
        files_seen: 25,
        files_total_estimate: 100,
      });
    });
    const bar1 = container.querySelector('[data-progress="active"]');
    expect(bar1).toBe(initialBar); // same DOM node
    expect(bar1!.getAttribute("data-percent")).toBe("25");

    // Second progress tick: 50 / 100 → 50%.
    act(() => {
      fire("scan.progress", {
        run_id: "r1",
        library_id: "lib-1",
        files_seen: 50,
        files_total_estimate: 100,
      });
    });
    const bar2 = container.querySelector('[data-progress="active"]');
    expect(bar2).toBe(initialBar);
    expect(bar2!.getAttribute("data-percent")).toBe("50");

    // Third progress tick: 75 / 100 → 75%.
    act(() => {
      fire("scan.progress", {
        run_id: "r1",
        library_id: "lib-1",
        files_seen: 75,
        files_total_estimate: 100,
      });
    });
    const bar3 = container.querySelector('[data-progress="active"]');
    expect(bar3).toBe(initialBar);
    expect(bar3!.getAttribute("data-percent")).toBe("75");
  });

  it("shows monotonically increasing counter text across three updates", () => {
    const { container } = render(
      <Wrapper>
        <BarHarness />
      </Wrapper>,
    );

    act(() => {
      fire("scan.started", { run_id: "r1", library_id: "lib-1" });
    });

    const readCounter = (): string => {
      // The counter is the last span inside the bar wrapper. We read
      // its text content rather than using getByText so we can call
      // it repeatedly inside the same render.
      const counters = container.querySelectorAll("span.tabular-nums");
      const last = counters[counters.length - 1];
      return last?.textContent?.trim() ?? "";
    };

    act(() => {
      fire("scan.progress", {
        run_id: "r1",
        library_id: "lib-1",
        files_seen: 25,
        files_total_estimate: 100,
      });
    });
    expect(readCounter()).toBe("25 / 100");

    act(() => {
      fire("scan.progress", {
        run_id: "r1",
        library_id: "lib-1",
        files_seen: 50,
        files_total_estimate: 100,
      });
    });
    expect(readCounter()).toBe("50 / 100");

    act(() => {
      fire("scan.progress", {
        run_id: "r1",
        library_id: "lib-1",
        files_seen: 99,
        files_total_estimate: 100,
      });
    });
    expect(readCounter()).toBe("99 / 100");
  });
});
