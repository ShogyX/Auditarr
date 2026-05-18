/**
 * v1.9 Stage 4.3 — per-row AND/OR combinator dropdown.
 *
 * The pre-1.9 ConditionRow rendered the conjunction as a static
 * label ("WHEN" / "AND" / "OR"). Stage 4.3 turns the non-first
 * rows into a clickable dropdown that flips the group combinator.
 *
 * The data model is unchanged — the builder is still flat with
 * one combinator per group. Per-row dropdowns mutate the shared
 * combinator (every row updates together). This matches the
 * operator's mental model ("I'm choosing how THIS row joins")
 * while keeping the JSON shape simple.
 *
 * Pins:
 *   1. First row renders the static "WHEN" label, no dropdown.
 *   2. Second row renders a select with AND / OR options.
 *   3. Flipping the second row's select calls onChange with the
 *      flipped top-level combinator.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
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

const VOCAB: RuleVocabulary = {
  fields: [
    { key: "extension", label: "Extension", type: "string", enum: null },
    { key: "container", label: "Container", type: "string", enum: null },
  ],
  ops: {
    string: ["eq", "ne", "in", "regex"],
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
  ],
  rule_flags: {},
};

const TWO_COND_RULE: RuleDefinition = {
  match: {
    all: [
      { field: "extension", op: "eq", value: "mkv" },
      { field: "container", op: "eq", value: "matroska" },
    ],
  },
  actions: [
    { type: "set_severity", severity: "warn" },
  ],
};

afterEach(() => {
  vi.clearAllMocks();
});

describe("v1.9 Stage 4.3 — per-row combinator dropdown", () => {
  it("renders static WHEN for the first row, dropdown for the second", () => {
    render(
      wrap(
        <VisualRuleBuilder
          definition={TWO_COND_RULE}
          vocabulary={VOCAB}
          onChange={vi.fn()}
        />,
      ),
    );
    // First row's conjunction is the static "WHEN" — NOT a select.
    expect(screen.getByText("WHEN")).toBeInTheDocument();
    // Second row's combinator IS a select with AND/OR.
    const combinatorSelects = screen.getAllByRole("combobox", {
      name: /combinator/i,
    });
    expect(combinatorSelects.length).toBe(1);
    expect(combinatorSelects[0] as HTMLSelectElement).toHaveValue("AND");
  });

  it("flipping the dropdown changes the parent group combinator", () => {
    const onChange = vi.fn();
    render(
      wrap(
        <VisualRuleBuilder
          definition={TWO_COND_RULE}
          vocabulary={VOCAB}
          onChange={onChange}
        />,
      ),
    );
    const combinator = screen.getByRole("combobox", { name: /combinator/i });
    fireEvent.change(combinator, { target: { value: "OR" } });
    expect(onChange).toHaveBeenCalled();
    // The mutated definition swaps the top-level `all` → `any`.
    const calls = onChange.mock.calls;
    const lastCall = calls[calls.length - 1]?.[0];
    expect(lastCall).toBeDefined();
    expect(lastCall.match.any).toBeDefined();
    expect(lastCall.match.all).toBeUndefined();
  });

  it("with three conditions, both the second and third rows render a dropdown", () => {
    const threeCondRule: RuleDefinition = {
      match: {
        all: [
          { field: "extension", op: "eq", value: "mkv" },
          { field: "extension", op: "eq", value: "mp4" },
          { field: "extension", op: "eq", value: "avi" },
        ],
      },
      actions: [{ type: "set_severity", severity: "warn" }],
    };
    render(
      wrap(
        <VisualRuleBuilder
          definition={threeCondRule}
          vocabulary={VOCAB}
          onChange={vi.fn()}
        />,
      ),
    );
    const combinatorSelects = screen.getAllByRole("combobox", {
      name: /combinator/i,
    });
    expect(combinatorSelects.length).toBe(2);
  });
});
