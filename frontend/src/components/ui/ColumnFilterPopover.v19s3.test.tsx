/**
 * v1.9 Stage 3.1 — ColumnFilterPopover contract.
 *
 * Pins:
 *   1. Closed state: only the trigger button is in the DOM.
 *   2. Opening the popover fires a /media/distinct fetch with the
 *      right field.
 *   3. The active-count badge surfaces on the trigger.
 *   4. Toggling a checkbox fires onToggle with the right value.
 *   5. The NULL bucket renders as "(none)" and toggles with the
 *      key "(none)" so the parent can store a sentinel for it.
 *   6. Include/Exclude radio toggle fires onModeChange.
 *   7. Clear button fires onClear (only visible when selected
 *      is non-empty).
 *   8. The truncated hint surfaces when the backend says so.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import type { ReactNode } from "react";

const apiGet = vi.fn();

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: (path: string) => apiGet(path),
  },
  ApiError: class extends Error {
    status = 500;
    code = "test";
  },
}));

import { ColumnFilterPopover } from "@/components/ui/ColumnFilterPopover";

function wrap(child: ReactNode): ReactNode {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return <QueryClientProvider client={qc}>{child}</QueryClientProvider>;
}

beforeEach(() => {
  apiGet.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("v1.9 Stage 3.1 — ColumnFilterPopover", () => {
  it("renders only the trigger when closed", () => {
    render(
      wrap(
        <ColumnFilterPopover
          field="severity"
          label="Severity"
          selected={new Set()}
          mode="include"
          onToggle={vi.fn()}
          onModeChange={vi.fn()}
          onClear={vi.fn()}
        />,
      ),
    );
    expect(
      screen.getByRole("button", { name: /filter severity/i }),
    ).toBeInTheDocument();
    // Popover content not in the DOM yet.
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opening fires a distinct fetch and renders the values", async () => {
    apiGet.mockResolvedValue({
      field: "severity",
      values: [
        { value: "ok", count: 80 },
        { value: "warn", count: 10 },
        { value: null, count: 5 },
      ],
      truncated: false,
    });
    render(
      wrap(
        <ColumnFilterPopover
          field="severity"
          label="Severity"
          selected={new Set()}
          mode="include"
          onToggle={vi.fn()}
          onModeChange={vi.fn()}
          onClear={vi.fn()}
        />,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: /filter severity/i }));
    await waitFor(() =>
      expect(apiGet).toHaveBeenCalledWith(
        expect.stringContaining("/media/distinct?field=severity"),
      ),
    );
    // Values render with counts.
    expect(await screen.findByText("ok")).toBeInTheDocument();
    expect(screen.getByText("warn")).toBeInTheDocument();
    // NULL bucket surfaces as "(none)".
    expect(screen.getByText("(none)")).toBeInTheDocument();
  });

  it("shows the active-count badge when selected is non-empty", () => {
    render(
      wrap(
        <ColumnFilterPopover
          field="severity"
          label="Severity"
          selected={new Set(["ok", "warn"])}
          mode="include"
          onToggle={vi.fn()}
          onModeChange={vi.fn()}
          onClear={vi.fn()}
        />,
      ),
    );
    // The trigger's badge is "2" — appears as a small text span
    // next to the filter icon.
    const trigger = screen.getByRole("button", { name: /filter severity/i });
    expect(trigger.textContent).toContain("2");
  });

  it("toggling a checkbox fires onToggle with the value key", async () => {
    apiGet.mockResolvedValue({
      field: "severity",
      values: [
        { value: "ok", count: 80 },
        { value: "warn", count: 10 },
      ],
      truncated: false,
    });
    const onToggle = vi.fn();
    render(
      wrap(
        <ColumnFilterPopover
          field="severity"
          label="Severity"
          selected={new Set()}
          mode="include"
          onToggle={onToggle}
          onModeChange={vi.fn()}
          onClear={vi.fn()}
        />,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: /filter severity/i }));
    const okCheckbox = await screen.findByRole("checkbox", { name: /ok/i });
    fireEvent.click(okCheckbox);
    expect(onToggle).toHaveBeenCalledWith("ok");
  });

  it("the NULL row toggles with the sentinel key '(none)'", async () => {
    apiGet.mockResolvedValue({
      field: "video_codec",
      values: [{ value: null, count: 12 }],
      truncated: false,
    });
    const onToggle = vi.fn();
    render(
      wrap(
        <ColumnFilterPopover
          field="video_codec"
          label="Codec"
          selected={new Set()}
          mode="include"
          onToggle={onToggle}
          onModeChange={vi.fn()}
          onClear={vi.fn()}
        />,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: /filter codec/i }));
    const noneCheckbox = await screen.findByRole("checkbox", { name: /\(none\)/i });
    fireEvent.click(noneCheckbox);
    expect(onToggle).toHaveBeenCalledWith("(none)");
  });

  it("flipping the mode toggle fires onModeChange", async () => {
    apiGet.mockResolvedValue({
      field: "severity",
      values: [{ value: "ok", count: 80 }],
      truncated: false,
    });
    const onModeChange = vi.fn();
    render(
      wrap(
        <ColumnFilterPopover
          field="severity"
          label="Severity"
          selected={new Set()}
          mode="include"
          onToggle={vi.fn()}
          onModeChange={onModeChange}
          onClear={vi.fn()}
        />,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: /filter severity/i }));
    const excludeRadio = await screen.findByRole("radio", { name: /exclude/i });
    fireEvent.click(excludeRadio);
    expect(onModeChange).toHaveBeenCalledWith("exclude");
  });

  it("Clear button appears only when selected is non-empty", async () => {
    apiGet.mockResolvedValue({
      field: "severity",
      values: [{ value: "ok", count: 80 }],
      truncated: false,
    });
    const onClear = vi.fn();

    // First render: empty selection → Clear NOT visible.
    const { rerender } = render(
      wrap(
        <ColumnFilterPopover
          field="severity"
          label="Severity"
          selected={new Set()}
          mode="include"
          onToggle={vi.fn()}
          onModeChange={vi.fn()}
          onClear={onClear}
        />,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: /filter severity/i }));
    await screen.findByText(/ok/);
    expect(screen.queryByRole("button", { name: /^clear$/i })).toBeNull();

    // Rerender with selection — Clear appears.
    rerender(
      wrap(
        <ColumnFilterPopover
          field="severity"
          label="Severity"
          selected={new Set(["ok"])}
          mode="include"
          onToggle={vi.fn()}
          onModeChange={vi.fn()}
          onClear={onClear}
        />,
      ),
    );
    const clearButton = screen.getByRole("button", { name: /^clear$/i });
    fireEvent.click(clearButton);
    expect(onClear).toHaveBeenCalled();
  });

  it("surfaces the truncated hint when backend says so", async () => {
    apiGet.mockResolvedValue({
      field: "filename",
      values: Array.from({ length: 200 }, (_, i) => ({
        value: `f${i}.mkv`,
        count: 1,
      })),
      truncated: true,
    });
    render(
      wrap(
        <ColumnFilterPopover
          field="filename"
          label="Filename"
          selected={new Set()}
          mode="include"
          onToggle={vi.fn()}
          onModeChange={vi.fn()}
          onClear={vi.fn()}
        />,
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: /filter filename/i }));
    expect(
      await screen.findByText(/more than 200 distinct values/i),
    ).toBeInTheDocument();
  });
});
