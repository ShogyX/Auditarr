/**
 * Stage 06 (v1.7) — VisualRuleBuilder additions.
 *
 * Pins the three new Stage 06 UX bits on the visual rule builder:
 *
 *   1. ``acknowledged_destructive`` checkbox surface (addendum
 *      A.0.1). Visible only when the rule contains a delete
 *      action; clicking it sets ``definition.acknowledged_
 *      destructive: true``; removing the last delete action
 *      strips the flag (so the backend's "forbidden on
 *      non-delete rules" branch doesn't fire).
 *
 *   2. Notify action's ``throttle`` block. Renders a collapsed
 *      toggle that expands into two numeric inputs
 *      (window_seconds, max_per_window). The checkbox writes
 *      ``throttle: { window_seconds, max_per_window }`` on the
 *      Notify action; clearing it writes ``throttle: null``.
 *
 *   3. New field options: ``probe_failed`` (bool) and
 *      ``vt_status`` (string with enum). The condition row's
 *      field dropdown lists them when present in the vocabulary;
 *      changing to ``vt_status`` reseeds the value to its first
 *      enum entry.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: vi.fn(async () => null),
    post: vi.fn(async () => null),
    put: vi.fn(async () => null),
    patch: vi.fn(async () => null),
    delete: vi.fn(async () => null),
  },
}));

vi.mock("@/stores/authStore", () => {
  const state = {
    accessToken: "tok",
    refreshToken: "ref",
    user: { id: "u1", role: "admin" as const, email: "a@b.c", username: "admin" },
    isHydrated: true,
    setTokens: vi.fn(),
    setSession: vi.fn(),
    setUser: vi.fn(),
    clear: vi.fn(),
    hydrate: vi.fn(),
  };
  type S = typeof state;
  const useAuthStore = vi.fn((sel?: (s: S) => unknown) =>
    typeof sel === "function" ? sel(state) : state,
  ) as unknown as ((sel?: (s: S) => unknown) => unknown) & {
    getState: () => S;
    persist: { hasHydrated: () => boolean };
  };
  useAuthStore.getState = () => state;
  useAuthStore.persist = { hasHydrated: () => true };
  return { useAuthStore };
});

vi.mock("@/lib/toast", () => ({ toast: vi.fn() }));

import { VisualRuleBuilder } from "@/features/rules/VisualRuleBuilder";
import type {
  RuleDefinition,
  RuleVocabulary,
} from "@/hooks/useRules";

function wrap(child: ReactNode): ReactNode {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter>{child}</MemoryRouter>
    </QueryClientProvider>
  );
}

// Stage 06 vocabulary — backend publishes:
//   * vt_status as string field with enum
//   * probe_failed as bool field
//   * Notify args_schema.throttle as object with two numeric props
//   * rule_flags.acknowledged_destructive entry
const VOCAB: RuleVocabulary = {
  fields: [
    { key: "extension", label: "Extension", type: "string", enum: null },
    { key: "probe_failed", label: "Probe failed", type: "bool", enum: null },
    {
      key: "vt_status",
      label: "Vt status",
      type: "string",
      enum: ["clean", "error", "malicious", "not_found", "suspicious"],
    },
  ],
  ops: {
    string: ["eq", "in", "ne", "regex"],
    numeric: ["eq", "gt", "gte", "lt", "lte", "ne"],
    bool: ["eq", "ne"],
    array: ["any_of", "contains", "none_of", "not_contains"],
  },
  severities: ["ok", "info", "warn", "high", "error", "crit"],
  actions: [
    {
      type: "set_severity",
      label: "Set severity",
      args_schema: {
        severity: {
          type: "string",
          enum: ["ok", "info", "warn", "high", "error", "crit"],
          required: true,
        },
      },
    },
    {
      type: "notify",
      label: "Notify",
      args_schema: {
        channel: { type: "string", required: true },
        message: { type: "string", required: false },
        throttle: {
          type: "object",
          required: false,
          hint: "Cap deliveries to N per rolling window.",
          properties: {
            window_seconds: { type: "numeric", minimum: 60, required: true },
            max_per_window: { type: "numeric", minimum: 1, required: true },
          },
        },
      },
    },
    {
      type: "delete",
      label: "Delete",
      args_schema: {
        reason: { type: "string", required: false },
      },
    },
  ],
  rule_flags: {
    acknowledged_destructive: {
      type: "bool",
      label: "I understand this rule deletes files from disk.",
      required_when: { any_action_type: "delete" },
      hint: "Auditarr's defensive layer for destructive rules.",
    },
  },
};

beforeEach(() => {
  /* noop */
});

afterEach(() => {
  vi.clearAllMocks();
});

// ── 1. acknowledged_destructive checkbox ───────────────────────

describe("Stage 06 — acknowledged_destructive checkbox", () => {
  it("is HIDDEN when the rule has no delete action", () => {
    const onChange = vi.fn();
    const definition: RuleDefinition = {
      match: { field: "extension", op: "eq", value: "mkv" },
      actions: [{ type: "set_severity", severity: "warn" }],
    };
    render(
      wrap(
        <VisualRuleBuilder
          definition={definition}
          vocabulary={VOCAB}
          onChange={onChange}
        />,
      ),
    );
    expect(
      screen.queryByTestId("acknowledged-destructive-section"),
    ).not.toBeInTheDocument();
  });

  it("is VISIBLE and unchecked when the rule has a delete action", () => {
    const onChange = vi.fn();
    const definition: RuleDefinition = {
      match: { field: "extension", op: "eq", value: "junk" },
      actions: [{ type: "delete", reason: null }],
    };
    render(
      wrap(
        <VisualRuleBuilder
          definition={definition}
          vocabulary={VOCAB}
          onChange={onChange}
        />,
      ),
    );
    const section = screen.getByTestId("acknowledged-destructive-section");
    expect(section).toBeInTheDocument();
    const checkbox = within(section).getByRole("checkbox") as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
    // Validation message visible.
    expect(section).toHaveTextContent(/will not save until acknowledged/i);
    // Label text comes from vocabulary.rule_flags.acknowledged_destructive.label.
    expect(section).toHaveTextContent(
      /I understand this rule deletes files from disk/i,
    );
  });

  it("toggles acknowledged_destructive: true on the definition when checked", () => {
    const onChange = vi.fn();
    const definition: RuleDefinition = {
      match: { field: "extension", op: "eq", value: "junk" },
      actions: [{ type: "delete", reason: null }],
    };
    render(
      wrap(
        <VisualRuleBuilder
          definition={definition}
          vocabulary={VOCAB}
          onChange={onChange}
        />,
      ),
    );
    const section = screen.getByTestId("acknowledged-destructive-section");
    const checkbox = within(section).getByRole("checkbox");
    fireEvent.click(checkbox);
    expect(onChange).toHaveBeenCalledTimes(1);
    const next = onChange.mock.calls[0]![0] as RuleDefinition;
    expect(next.acknowledged_destructive).toBe(true);
  });

  it("strips acknowledged_destructive when the last delete action is removed", () => {
    const onChange = vi.fn();
    const definition: RuleDefinition = {
      match: { field: "extension", op: "eq", value: "junk" },
      // Two actions so the "remove" button is enabled on each.
      actions: [
        { type: "delete", reason: null },
        { type: "set_severity", severity: "warn" },
      ],
      acknowledged_destructive: true,
    };
    render(
      wrap(
        <VisualRuleBuilder
          definition={definition}
          vocabulary={VOCAB}
          onChange={onChange}
        />,
      ),
    );
    // Click the trash icon button on the FIRST action row (delete).
    const removeButtons = screen.getAllByTitle("Remove action");
    fireEvent.click(removeButtons[0]!);
    expect(onChange).toHaveBeenCalled();
    const next = onChange.mock.calls[0]![0] as RuleDefinition;
    // No delete action remains AND the ack flag is stripped.
    expect(next.actions.some((a) => a.type === "delete")).toBe(false);
    expect(next.acknowledged_destructive).toBeUndefined();
  });
});

// ── 2. Notify throttle inputs ──────────────────────────────────

describe("Stage 06 — Notify throttle inputs", () => {
  it("does not render throttle inputs by default (collapsed)", () => {
    const onChange = vi.fn();
    const definition: RuleDefinition = {
      match: { field: "extension", op: "eq", value: "mkv" },
      actions: [{ type: "notify", channel: "ops", message: null }],
    };
    render(
      wrap(
        <VisualRuleBuilder
          definition={definition}
          vocabulary={VOCAB}
          onChange={onChange}
        />,
      ),
    );
    // The throttle toggle is rendered as a checkbox with label
    // "throttle" — but the inner number inputs only appear once
    // the checkbox is checked.
    expect(screen.queryByLabelText(/window_seconds/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/max_per_window/i)).not.toBeInTheDocument();
  });

  it("seeds throttle with the schema minimums when the toggle is enabled", () => {
    const onChange = vi.fn();
    const definition: RuleDefinition = {
      match: { field: "extension", op: "eq", value: "mkv" },
      actions: [{ type: "notify", channel: "ops", message: null }],
    };
    render(
      wrap(
        <VisualRuleBuilder
          definition={definition}
          vocabulary={VOCAB}
          onChange={onChange}
        />,
      ),
    );
    // Find the throttle toggle (the checkbox sibling of the
    // "throttle" label).
    const throttleLabel = screen.getByText("throttle");
    const toggle = throttleLabel.parentElement?.querySelector(
      "input[type='checkbox']",
    ) as HTMLInputElement | null;
    expect(toggle).toBeTruthy();
    fireEvent.click(toggle!);
    expect(onChange).toHaveBeenCalled();
    const next = onChange.mock.calls[0]![0] as RuleDefinition;
    const action = next.actions[0]!;
    expect(action.type).toBe("notify");
    if (action.type === "notify") {
      expect(action.throttle).toEqual({
        window_seconds: 60,
        max_per_window: 1,
      });
    }
  });

  it("renders the two numeric inputs when throttle is set and updates them", () => {
    const onChange = vi.fn();
    const definition: RuleDefinition = {
      match: { field: "extension", op: "eq", value: "mkv" },
      actions: [
        {
          type: "notify",
          channel: "ops",
          message: null,
          throttle: { window_seconds: 300, max_per_window: 5 },
        },
      ],
    };
    render(
      wrap(
        <VisualRuleBuilder
          definition={definition}
          vocabulary={VOCAB}
          onChange={onChange}
        />,
      ),
    );
    const wsInput = screen.getByLabelText(/window_seconds/i) as HTMLInputElement;
    const mpwInput = screen.getByLabelText(/max_per_window/i) as HTMLInputElement;
    expect(wsInput.value).toBe("300");
    expect(mpwInput.value).toBe("5");
    // Update window_seconds to 600.
    fireEvent.change(wsInput, { target: { value: "600" } });
    expect(onChange).toHaveBeenCalled();
    const next = onChange.mock.calls[0]![0] as RuleDefinition;
    const action = next.actions[0]!;
    expect(action.type).toBe("notify");
    if (action.type === "notify") {
      expect(action.throttle).toEqual({
        window_seconds: 600,
        max_per_window: 5,
      });
    }
  });

  it("clears throttle to null when the toggle is unchecked", () => {
    const onChange = vi.fn();
    const definition: RuleDefinition = {
      match: { field: "extension", op: "eq", value: "mkv" },
      actions: [
        {
          type: "notify",
          channel: "ops",
          message: null,
          throttle: { window_seconds: 300, max_per_window: 5 },
        },
      ],
    };
    render(
      wrap(
        <VisualRuleBuilder
          definition={definition}
          vocabulary={VOCAB}
          onChange={onChange}
        />,
      ),
    );
    const throttleLabel = screen.getByText("throttle");
    const toggle = throttleLabel.parentElement?.querySelector(
      "input[type='checkbox']",
    ) as HTMLInputElement | null;
    expect(toggle?.checked).toBe(true);
    fireEvent.click(toggle!);
    const next = onChange.mock.calls[0]![0] as RuleDefinition;
    const action = next.actions[0]!;
    expect(action.type).toBe("notify");
    if (action.type === "notify") {
      expect(action.throttle).toBeNull();
    }
  });
});

// ── 3. New field options (probe_failed, vt_status) ─────────────

describe("Stage 06 — new field options", () => {
  it("offers probe_failed and vt_status in the field picker", () => {
    const onChange = vi.fn();
    const definition: RuleDefinition = {
      match: { field: "extension", op: "eq", value: "mkv" },
      actions: [{ type: "set_severity", severity: "warn" }],
    };
    render(
      wrap(
        <VisualRuleBuilder
          definition={definition}
          vocabulary={VOCAB}
          onChange={onChange}
        />,
      ),
    );
    // The condition row renders a <select> for the field. Both
    // new options appear; the labels are taken from vocabulary.
    const fieldSelect = screen.getByLabelText(/Field/i) as HTMLSelectElement;
    const options = Array.from(fieldSelect.options).map((o) => o.value);
    expect(options).toContain("probe_failed");
    expect(options).toContain("vt_status");
  });
});
