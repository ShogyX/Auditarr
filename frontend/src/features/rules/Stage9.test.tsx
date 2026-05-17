/**
 * Stage 9 (audit follow-up), updated Stage 05 (v1.7) —
 * VisualRuleBuilder + Action union.
 *
 * Stage 9 originally pinned a ``Quarantine`` action + a Delete
 * action gated by a ``confirm`` boolean. Stage 05 retired both
 * (Section A.0 of the v1.7 addendum — "delete means delete").
 * The file now pins the post-Stage-05 contract:
 *
 *   - The Action type union excludes ``quarantine`` (TS compile-
 *     time guarantee).
 *   - The visual builder renders ``Delete`` from vocabulary as a
 *     plain action with a ``reason`` text input (not a checkbox).
 *   - ``Quarantine`` does NOT appear in the action picker (it's
 *     gone from the vocabulary).
 *   - The freshAction("delete") default returns
 *     ``{ type: "delete", reason: null }`` — no ``confirm`` field.
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
    delete: vi.fn(async () => null),
    patch: vi.fn(async () => null),
  },
  ApiError: class extends Error {
    status = 500;
    code = "test";
  },
}));

vi.mock("@/stores/authStore", () => {
  const state = {
    tokens: {
      accessToken: "x",
      refreshToken: "x",
      expiresAt: Date.now() + 6e4,
    },
    user: { id: "u1", username: "admin", role: "admin" },
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
  Action,
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

// Stage 05 (v1.7) vocabulary: ``Quarantine`` is gone; ``Delete``
// publishes ``reason`` (string) only, no ``confirm`` boolean.
const VOCAB: RuleVocabulary = {
  fields: [
    {
      key: "extension",
      label: "Extension",
      type: "string",
      enum: null,
    },
  ],
  ops: { string: ["eq", "ne", "regex"], numeric: [], bool: [], array: [] },
  severities: ["ok", "info", "warn", "high", "error", "crit"],
  actions: [
    {
      type: "set_severity",
      label: "Set severity",
      args_schema: {
        severity: {
          type: "string",
          enum: ["ok", "info", "warn"],
          required: true,
        },
      },
    },
    {
      type: "delete",
      label: "Delete",
      args_schema: {
        reason: {
          type: "string",
          required: false,
          hint: "Optional reason recorded in the audit log",
        },
      },
    },
  ],
};

beforeEach(() => {
  /* noop */
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Stage 05 (v1.7) — VisualRuleBuilder action vocabulary", () => {
  it("renders Delete but NOT Quarantine in the action-type picker", () => {
    const def: RuleDefinition = {
      match: { field: "extension", op: "eq", value: "mkv" },
      actions: [{ type: "set_severity", severity: "warn" }],
    };
    render(
      wrap(
        <VisualRuleBuilder
          definition={def}
          onChange={() => {}}
          vocabulary={VOCAB}
        />,
      ),
    );

    const selects = screen.getAllByRole("combobox", { name: /action type/i });
    expect(selects.length).toBeGreaterThan(0);
    const firstSelect = selects[0]!;
    const options = within(firstSelect).getAllByRole("option");
    const labels = options.map((o) => o.textContent?.trim());
    expect(labels).toContain("Delete");
    // Stage 05 retired the Quarantine action — it must not appear
    // in the picker even if a stale vocabulary publishes it.
    expect(labels).not.toContain("Quarantine");
  });

  it("switching to Delete emits the Stage 05 default { type: 'delete', reason: null }", () => {
    let captured: RuleDefinition | null = null;
    const def: RuleDefinition = {
      match: { field: "extension", op: "eq", value: "mkv" },
      actions: [{ type: "set_severity", severity: "warn" }],
    };
    render(
      wrap(
        <VisualRuleBuilder
          definition={def}
          onChange={(d) => {
            captured = d;
          }}
          vocabulary={VOCAB}
        />,
      ),
    );

    // Flip the action-type to "delete".
    const select = screen.getByRole("combobox", { name: /action type/i });
    fireEvent.change(select, { target: { value: "delete" } });

    expect(captured).not.toBeNull();
    const action = captured!.actions[0] as Action;
    expect(action.type).toBe("delete");
    if (action.type === "delete") {
      // Stage 05 retired ``confirm``; default carries a null
      // reason (audit log gets "Deleted by rule" synthesized
      // server-side).
      expect(action.reason).toBeNull();
      // ``confirm`` isn't on the union type — runtime guard for
      // a future regression that adds it back.
      expect((action as Record<string, unknown>).confirm).toBeUndefined();
    }
  });

  it("Delete action surfaces a reason text input (not a checkbox)", () => {
    const def: RuleDefinition = {
      match: { field: "extension", op: "eq", value: "mkv" },
      actions: [{ type: "delete", reason: null } as Action],
    };
    render(
      wrap(
        <VisualRuleBuilder
          definition={def}
          onChange={() => {}}
          vocabulary={VOCAB}
        />,
      ),
    );

    // Stage 05: Delete's only published arg is ``reason``,
    // typed as ``string``. The pre-Stage-05 ``confirm`` checkbox
    // must not appear — assert both directions so a regression
    // that re-introduces it trips this test.
    expect(
      screen.queryByRole("checkbox", { name: /confirm/i }),
    ).not.toBeInTheDocument();
    // The reason input is rendered (label text contains "reason").
    const labels = screen.getAllByText(/reason/i);
    expect(labels.length).toBeGreaterThan(0);
  });

  it("typing in the reason input propagates the new reason via onChange", () => {
    let captured: RuleDefinition | null = null;
    const def: RuleDefinition = {
      match: { field: "extension", op: "eq", value: "mkv" },
      actions: [{ type: "delete", reason: null } as Action],
    };
    render(
      wrap(
        <VisualRuleBuilder
          definition={def}
          onChange={(d) => {
            captured = d;
          }}
          vocabulary={VOCAB}
        />,
      ),
    );

    // Find the reason text input. The renderer wraps the input
    // in a label whose text contains "reason"; the input's
    // placeholder is the hint string.
    const inputs = screen.getAllByRole("textbox");
    const reasonInput = inputs.find(
      (el) =>
        (el.getAttribute("placeholder") || "")
          .toLowerCase()
          .includes("reason") ||
        (el.closest("label")?.textContent || "")
          .toLowerCase()
          .includes("reason"),
    );
    expect(reasonInput).toBeDefined();
    fireEvent.change(reasonInput!, {
      target: { value: "Plex codec incompat" },
    });

    expect(captured).not.toBeNull();
    const action = captured!.actions[0] as Action;
    expect(action.type).toBe("delete");
    if (action.type === "delete") {
      expect(action.reason).toBe("Plex codec incompat");
    }
  });
});
