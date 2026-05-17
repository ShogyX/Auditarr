/**
 * Stage 10 (v1.7) — VirusTotalCard rendering tests.
 *
 * Covers:
 *   - Loading state.
 *   - Empty-state (no integration configured).
 *   - Configured-but-disabled state.
 *   - Active state with all three quota bars + queue size.
 *   - Three-window quota rendering (addendum B.7).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import type { VirusTotalStatus } from "@/hooks/useIntegrations";

let __nextStatus: VirusTotalStatus | undefined;
let __nextState: "loading" | "error" | "ok" = "ok";

vi.mock("@/hooks/useIntegrations", async () => {
  const actual: Record<string, unknown> = await vi.importActual(
    "@/hooks/useIntegrations",
  );
  return {
    ...actual,
    useVirustotalStatus: () => {
      if (__nextState === "loading") {
        return {
          data: undefined,
          isLoading: true,
          isError: false,
          error: null,
        };
      }
      if (__nextState === "error") {
        return {
          data: undefined,
          isLoading: false,
          isError: true,
          error: new Error("boom"),
        };
      }
      return {
        data: __nextStatus,
        isLoading: false,
        isError: false,
        error: null,
      };
    },
  };
});

import { VirusTotalCard } from "@/features/integrations/VirusTotalCard";

function wrap(child: ReactNode): ReactNode {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return <QueryClientProvider client={qc}>{child}</QueryClientProvider>;
}

const BASE_STATUS: VirusTotalStatus = {
  minute_used: 0,
  minute_cap: 4,
  minute_remaining: 4,
  day_used: 0,
  day_cap: 500,
  day_remaining: 500,
  month_used: 0,
  month_cap: 15500,
  month_remaining: 15500,
  quota_used_today: 0,
  quota_limit: 500,
  queue_size: 0,
  last_check_at: null,
  enabled: true,
  configured: true,
};

describe("VirusTotalCard (Stage 10)", () => {
  it("renders the loading state during the first poll", () => {
    __nextState = "loading";
    render(wrap(<VirusTotalCard />));
    expect(
      screen.getByText(/loading virustotal status/i),
    ).toBeInTheDocument();
  });

  it("renders the empty state when no VT integration is configured", () => {
    __nextState = "ok";
    __nextStatus = { ...BASE_STATUS, configured: false, enabled: false };
    render(wrap(<VirusTotalCard />));
    expect(
      screen.getByText(/no virustotal integration configured/i),
    ).toBeInTheDocument();
  });

  it("renders the disabled state when configured but disabled", () => {
    __nextState = "ok";
    __nextStatus = { ...BASE_STATUS, configured: true, enabled: false };
    render(wrap(<VirusTotalCard />));
    // The subtitle line is the unambiguous indicator.
    expect(
      screen.getByText(/configured but disabled/i),
    ).toBeInTheDocument();
    // The pill carries the lowercase label.
    expect(screen.getByText(/^disabled$/i)).toBeInTheDocument();
  });

  it("renders all three quota windows (addendum B.7)", () => {
    __nextState = "ok";
    __nextStatus = {
      ...BASE_STATUS,
      minute_used: 2,
      day_used: 100,
      month_used: 1000,
    };
    render(wrap(<VirusTotalCard />));

    // All three window labels present.
    expect(screen.getByText("Per-minute")).toBeInTheDocument();
    expect(screen.getByText("Per-day")).toBeInTheDocument();
    expect(screen.getByText("Per-month")).toBeInTheDocument();

    // Each window's used/cap rendered.
    expect(screen.getByText("2 / 4")).toBeInTheDocument();
    expect(screen.getByText("100 / 500")).toBeInTheDocument();
    expect(screen.getByText("1,000 / 15,500")).toBeInTheDocument();

    // Each window's bar has the right aria-valuemax.
    const minuteBar = screen.getByLabelText(/per-minute quota usage/i);
    expect(minuteBar).toHaveAttribute("aria-valuenow", "2");
    expect(minuteBar).toHaveAttribute("aria-valuemax", "4");
  });

  it("renders the queue size with the test id for stable targeting", () => {
    __nextState = "ok";
    __nextStatus = { ...BASE_STATUS, queue_size: 42 };
    render(wrap(<VirusTotalCard />));
    const queue = screen.getByTestId("virustotal-queue-size");
    expect(queue).toHaveTextContent("42");
  });

  it("surfaces last_check_at when present", () => {
    __nextState = "ok";
    __nextStatus = {
      ...BASE_STATUS,
      last_check_at: "2026-05-16T12:00:00Z",
    };
    render(wrap(<VirusTotalCard />));
    expect(screen.getByText(/last lookup/i)).toBeInTheDocument();
  });

  it("surfaces 'No lookups yet' when last_check_at is null", () => {
    __nextState = "ok";
    __nextStatus = { ...BASE_STATUS, last_check_at: null };
    render(wrap(<VirusTotalCard />));
    expect(
      screen.getByText(/no lookups yet this session/i),
    ).toBeInTheDocument();
  });
});
