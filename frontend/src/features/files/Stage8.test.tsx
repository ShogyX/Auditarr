/**
 * Stage 8 (audit follow-up) — Scan progress bar + scan-all dropdown.
 *
 * Pins:
 *   - ScanProgressBar renders nothing when idle.
 *   - ScanProgressBar shows the indeterminate state when running
 *     without a total estimate.
 *   - ScanProgressBar shows percent when total estimate available.
 *   - ScanProgressBar shows "Scan complete" pill on recentlyCompleted.
 *   - FilesRunScanButton's chevron opens the dropdown menu.
 *   - Clicking "Scan all libraries" calls onScanAll and closes the menu.
 *   - The chevron stays enabled even when libraryId is empty.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

// Mock useScanProgress so we can drive the bar's input directly.
let mockProgress = {
  runId: null as string | null,
  libraryId: null as string | null,
  filesSeen: 0,
  filesTotalEstimate: null as number | null,
  percent: null as number | null,
  recentlyCompleted: false,
};

vi.mock("@/hooks/useScanProgress", () => ({
  useScanProgress: () => mockProgress,
}));

import { ScanProgressBar } from "@/components/ui/ScanProgressBar";
import { FilesRunScanButton } from "@/features/files/FilesRunScanButton";

beforeEach(() => {
  mockProgress = {
    runId: null,
    libraryId: null,
    filesSeen: 0,
    filesTotalEstimate: null,
    percent: null,
    recentlyCompleted: false,
  };
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Stage 8 — ScanProgressBar", () => {
  it("renders nothing when idle (no run, not recently completed)", () => {
    const { container } = render(<ScanProgressBar />);
    expect(container.firstChild).toBeNull();
  });

  it("renders indeterminate state when running without a total estimate", () => {
    mockProgress = {
      ...mockProgress,
      runId: "run-1",
      libraryId: "lib-1",
      filesSeen: 0,
      filesTotalEstimate: null,
      percent: null,
    };
    render(<ScanProgressBar />);
    // The indeterminate state surfaces an "Enumerating..." pill.
    expect(screen.getByText(/enumerating/i)).toBeInTheDocument();
  });

  it("renders the Scanning pill when running with percent", () => {
    mockProgress = {
      ...mockProgress,
      runId: "run-1",
      libraryId: "lib-1",
      filesSeen: 250,
      filesTotalEstimate: 1000,
      percent: 25,
    };
    render(<ScanProgressBar />);
    expect(screen.getByText(/^scanning$/i)).toBeInTheDocument();
  });

  it("renders the Scan complete pill on recentlyCompleted", () => {
    mockProgress = {
      ...mockProgress,
      runId: null,
      filesSeen: 1234,
      filesTotalEstimate: 1234,
      percent: 100,
      recentlyCompleted: true,
    };
    render(<ScanProgressBar />);
    expect(screen.getByText(/scan complete/i)).toBeInTheDocument();
  });

  it("has aria-live polite for screen reader announcements", () => {
    mockProgress = {
      ...mockProgress,
      runId: "run-1",
      filesSeen: 10,
      filesTotalEstimate: 100,
      percent: 10,
    };
    render(<ScanProgressBar />);
    const status = screen.getByRole("status");
    expect(status.getAttribute("aria-live")).toBe("polite");
  });
});

describe("Stage 8 — FilesRunScanButton dropdown", () => {
  it("renders the primary Run scan button and the chevron", () => {
    render(
      <FilesRunScanButton
        libraryId="lib-1"
        disabled={false}
        isPending={false}
        onRun={vi.fn()}
        onScanAll={vi.fn()}
      />,
    );
    expect(
      screen.getByRole("button", { name: /run scan/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /more scan options/i }),
    ).toBeInTheDocument();
  });

  it("the chevron stays enabled even when libraryId is empty (scan-all needs no library)", () => {
    render(
      <FilesRunScanButton
        libraryId=""
        disabled={true}
        isPending={false}
        onRun={vi.fn()}
        onScanAll={vi.fn()}
      />,
    );
    const chev = screen.getByRole("button", { name: /more scan options/i });
    expect(chev).not.toBeDisabled();
  });

  it("clicking the chevron opens the menu", () => {
    render(
      <FilesRunScanButton
        libraryId="lib-1"
        disabled={false}
        isPending={false}
        onRun={vi.fn()}
        onScanAll={vi.fn()}
      />,
    );
    // Menu not present initially.
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: /more scan options/i }),
    );
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(
      screen.getByRole("menuitem", { name: /scan all libraries/i }),
    ).toBeInTheDocument();
  });

  it("clicking 'Scan all libraries' calls onScanAll and closes the menu", () => {
    const onScanAll = vi.fn();
    render(
      <FilesRunScanButton
        libraryId="lib-1"
        disabled={false}
        isPending={false}
        onRun={vi.fn()}
        onScanAll={onScanAll}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /more scan options/i }),
    );
    fireEvent.click(
      screen.getByRole("menuitem", { name: /scan all libraries/i }),
    );
    expect(onScanAll).toHaveBeenCalledTimes(1);
    // Menu should be gone afterward.
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("clicking Run scan calls onRun with the current library id", () => {
    const onRun = vi.fn();
    render(
      <FilesRunScanButton
        libraryId="lib-1"
        disabled={false}
        isPending={false}
        onRun={onRun}
        onScanAll={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /run scan/i }));
    expect(onRun).toHaveBeenCalledWith("lib-1");
  });

  it("does not invoke onRun when libraryId is empty", () => {
    const onRun = vi.fn();
    render(
      <FilesRunScanButton
        libraryId=""
        disabled={true}
        isPending={false}
        onRun={onRun}
        onScanAll={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /run scan/i }));
    expect(onRun).not.toHaveBeenCalled();
  });
});
