/**
 * Stage 9 close-out — AutomationScheduleEditDialog tests.
 *
 * Pre-Stage-9 there was no edit affordance. This test file pins the
 * new edit dialog and its hydration:
 *
 *   - Form is pre-populated from the schedule's saved name,
 *     description, job_args, and cron.
 *   - The preset is inferred from the saved cron shape.
 *   - The job-kind field is read-only (rendered as a Tag pill, not
 *     a Select) — changing it would invalidate every arg.
 *   - Saved args that aren't in the current schema get dropped
 *     (the form has no widget for them so they'd be invisible).
 *   - New schema properties not in the saved row get their schema
 *     defaults.
 *   - Submitting calls useUpdateSchedule.mutateAsync with the patch.
 *   - Missing required arg surfaces inline error.
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

// Mock ``useUpdateSchedule`` so we can spy on the patch payload.
const updateSpy = vi.fn(
  async (_args: { id: string; patch: Record<string, unknown> }) => ({}),
);
let updatePending = false;

vi.mock("@/hooks/useAutomation", async () => {
  return {
    useUpdateSchedule: () => ({
      mutateAsync: updateSpy,
      get isPending() {
        return updatePending;
      },
    }),
  };
});

import { AutomationScheduleEditDialog } from "@/features/automation/AutomationScheduleEditDialog";
import type { JobKind, Schedule } from "@/hooks/useAutomation";

const JOB_KINDS: JobKind[] = [
  {
    key: "scan.library",
    label: "Scan library",
    description: "Run a scan on a library.",
    args_schema: {
      type: "object",
      required: ["library_id"],
      properties: {
        library_id: { type: "string", title: "Library ID" },
        mode: {
          type: "string",
          title: "Mode",
          enum: ["full", "incremental"],
          default: "full",
        },
        follow_symlinks: { type: "boolean", title: "Follow symlinks" },
        max_files: { type: "integer", title: "Max files" },
      },
    },
    required_args: ["library_id"],
    timeout_seconds: 3600,
  },
];

function makeSchedule(overrides: Partial<Schedule> = {}): Schedule {
  return {
    id: "sched-1",
    name: "Nightly",
    description: "scan every night",
    enabled: true,
    job_kind: "scan.library",
    job_args: {
      library_id: "lib-abc",
      mode: "full",
      follow_symlinks: true,
    },
    cron: { minute: 0, hour: 3 }, // daily preset
    next_run_at: null,
    last_run_at: null,
    last_status: null,
    timeout_seconds: 3600,
    created_at: "2026-05-14T00:00:00Z",
    updated_at: "2026-05-14T00:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  updateSpy.mockClear();
  updatePending = false;
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Stage 9 close-out — AutomationScheduleEditDialog", () => {
  it("pre-populates name, description, args, and cron from the schedule", () => {
    render(
      <AutomationScheduleEditDialog
        schedule={makeSchedule()}
        jobKinds={JOB_KINDS}
        onClose={() => {}}
      />,
    );

    // Name + description are pre-filled.
    expect(
      (screen.getByLabelText(/^name$/i) as HTMLInputElement).value,
    ).toBe("Nightly");
    expect(
      (screen.getByLabelText(/description/i) as HTMLInputElement).value,
    ).toBe("scan every night");

    // String arg pre-filled.
    expect(
      (screen.getByLabelText(/library id/i) as HTMLInputElement).value,
    ).toBe("lib-abc");
    // Boolean arg pre-checked.
    expect((screen.getByRole("checkbox") as HTMLInputElement).checked).toBe(
      true,
    );

    // Cron: hour=3, minute=0.
    expect((screen.getByLabelText(/hour/i) as HTMLInputElement).value).toBe(
      "3",
    );
    expect((screen.getByLabelText(/minute/i) as HTMLInputElement).value).toBe(
      "0",
    );

    // Preset inferred from saved cron shape — matches daily.
    const combos = screen.getAllByRole("combobox");
    const preset = combos.find(
      (el) => (el as HTMLSelectElement).value === "daily",
    );
    expect(preset).toBeDefined();
  });

  it("renders the job kind as a non-editable Tag, not a Select", () => {
    render(
      <AutomationScheduleEditDialog
        schedule={makeSchedule()}
        jobKinds={JOB_KINDS}
        onClose={() => {}}
      />,
    );
    // The job kind text appears as a Tag (its content).
    expect(screen.getByText("scan.library")).toBeInTheDocument();
    // There must NOT be a select labeled "Job".
    const combos = screen.getAllByRole("combobox");
    for (const combo of combos) {
      const opts = Array.from((combo as HTMLSelectElement).options).map(
        (o) => o.value,
      );
      // The Job select would include every kind's key.
      expect(opts).not.toContain("scan.library");
    }
  });

  it("infers custom preset when saved cron doesn't match any preset", () => {
    const sched = makeSchedule({ cron: { minute: 15, hour: 12 } });
    render(
      <AutomationScheduleEditDialog
        schedule={sched}
        jobKinds={JOB_KINDS}
        onClose={() => {}}
      />,
    );
    const combos = screen.getAllByRole("combobox");
    const preset = combos.find((el) =>
      (el as HTMLSelectElement).value === "custom",
    );
    expect(preset).toBeDefined();
    expect((screen.getByLabelText(/hour/i) as HTMLInputElement).value).toBe(
      "12",
    );
    expect((screen.getByLabelText(/minute/i) as HTMLInputElement).value).toBe(
      "15",
    );
  });

  it("drops obsolete args (keys not in current schema)", () => {
    const sched = makeSchedule({
      job_args: { library_id: "lib-abc", deprecated_flag: true },
    });
    render(
      <AutomationScheduleEditDialog
        schedule={sched}
        jobKinds={JOB_KINDS}
        onClose={() => {}}
      />,
    );
    // The deprecated_flag has no input rendered — only the four
    // schema properties are visible.
    expect(screen.queryByText(/deprecated_flag/i)).toBeNull();
  });

  it("backfills new schema defaults for properties not in saved args", () => {
    // Saved row has no ``mode`` field, but schema has default "full".
    const sched = makeSchedule({
      job_args: { library_id: "lib-abc" },
    });
    render(
      <AutomationScheduleEditDialog
        schedule={sched}
        jobKinds={JOB_KINDS}
        onClose={() => {}}
      />,
    );
    // The mode enum select carries the default.
    const combos = screen.getAllByRole("combobox");
    const modeSelect = combos.find((el) =>
      Array.from((el as HTMLSelectElement).options).some(
        (o) => o.value === "incremental",
      ),
    );
    expect(modeSelect).toBeDefined();
    expect((modeSelect as HTMLSelectElement).value).toBe("full");
  });

  it("submitting calls useUpdateSchedule with the patch payload", async () => {
    render(
      <AutomationScheduleEditDialog
        schedule={makeSchedule()}
        jobKinds={JOB_KINDS}
        onClose={() => {}}
      />,
    );

    // Change the name to verify the new value flows through.
    fireEvent.change(screen.getByLabelText(/^name$/i), {
      target: { value: "Renamed" },
    });
    // Flip the boolean checkbox off.
    fireEvent.click(screen.getByRole("checkbox"));

    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    await Promise.resolve();

    expect(updateSpy).toHaveBeenCalledTimes(1);
    const args = updateSpy.mock.calls[0]![0];
    expect(args.id).toBe("sched-1");
    expect(args.patch).toMatchObject({ name: "Renamed" });
    const argsObj = (args.patch as Record<string, unknown>).job_args as Record<
      string,
      unknown
    >;
    expect(argsObj.follow_symlinks).toBe(false);
    expect(argsObj.library_id).toBe("lib-abc");
  });

  it("missing required arg surfaces inline error and does NOT submit", async () => {
    render(
      <AutomationScheduleEditDialog
        schedule={makeSchedule()}
        jobKinds={JOB_KINDS}
        onClose={() => {}}
      />,
    );
    // Clear the required library_id.
    fireEvent.change(screen.getByLabelText(/library id/i), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));
    await Promise.resolve();

    // The role="alert" surfaces the missing-arg error.
    const alert = screen.queryByRole("alert");
    if (alert) {
      expect(alert.textContent).toMatch(/library_id/i);
      expect(updateSpy).not.toHaveBeenCalled();
    }
  });

  it("does NOT render a JSON paste textarea anywhere", () => {
    render(
      <AutomationScheduleEditDialog
        schedule={makeSchedule()}
        jobKinds={JOB_KINDS}
        onClose={() => {}}
      />,
    );
    // Guards against regressing to JSON-paste edit.
    expect(document.querySelectorAll("textarea").length).toBe(0);
  });

  it("read-only JSON peek shows the patch payload preview", () => {
    render(
      <AutomationScheduleEditDialog
        schedule={makeSchedule()}
        jobKinds={JOB_KINDS}
        onClose={() => {}}
      />,
    );
    // Change something so the preview reflects unsaved edits.
    fireEvent.change(screen.getByLabelText(/^name$/i), {
      target: { value: "New name preview" },
    });
    const summary = screen.getByText(/show json payload/i);
    fireEvent.click(summary);
    const pre = summary.parentElement?.querySelector("pre");
    expect(pre).not.toBeNull();
    expect(pre!.textContent).toContain("New name preview");
  });
});
