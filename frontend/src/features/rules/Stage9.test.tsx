/**
 * Stage 9 (audit follow-up) — VisualRuleBuilder + Action union.
 *
 * Pins:
 *   - Action type union includes ``quarantine`` and ``delete`` so
 *     the discriminated shape compiles cleanly.
 *   - The visual builder renders vocabulary actions including the
 *     two new types when the backend reports them.
 *   - Selecting ``delete`` exposes a confirm checkbox (boolean arg).
 *   - The confirm checkbox starts unchecked (safe default).
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

// Mirror the backend's Stage 9 vocabulary endpoint output.
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
        severity: { type: "string", enum: ["ok", "info", "warn"], required: true },
      },
    },
    {
      type: "quarantine",
      label: "Quarantine",
      args_schema: {
        reason: { type: "string", required: false, hint: "Optional reason" },
      },
    },
    {
      type: "delete",
      label: "Delete",
      args_schema: {
        confirm: {
          type: "boolean",
          required: false,
          hint: "Required for HARD delete",
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

describe("Stage 9 — VisualRuleBuilder action vocabulary", () => {
  it("renders Quarantine and Delete in the action-type picker", () => {
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

    // The action-type <select> shows the three Stage 9 vocabulary
    // actions (including the new ones).
    const selects = screen.getAllByRole("combobox", { name: /action type/i });
    expect(selects.length).toBeGreaterThan(0);
    const firstSelect = selects[0]!;
    const options = within(firstSelect).getAllByRole("option");
    const labels = options.map((o) => o.textContent?.trim());
    expect(labels).toContain("Quarantine");
    expect(labels).toContain("Delete");
  });

  it("switching to Delete reveals a confirm checkbox at unchecked default", () => {
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

    // onChange should be called with a new definition whose action
    // is the freshAction("delete") shape: confirm=false.
    expect(captured).not.toBeNull();
    const action = captured!.actions[0] as Action;
    expect(action.type).toBe("delete");
    // Discriminated shape — the type narrowing kicks in here.
    if (action.type === "delete") {
      expect(action.confirm).toBe(false);
    }
  });

  it("delete action's confirm checkbox is rendered when active", () => {
    const def: RuleDefinition = {
      match: { field: "extension", op: "eq", value: "mkv" },
      actions: [{ type: "delete", confirm: false } as Action],
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

    // The confirm input is rendered as a checkbox.
    const checkbox = screen.getByRole("checkbox", { name: /confirm/i });
    expect(checkbox).toBeInTheDocument();
    expect((checkbox as HTMLInputElement).checked).toBe(false);
  });

  it("checking the confirm checkbox propagates confirm=true via onChange", () => {
    let captured: RuleDefinition | null = null;
    const def: RuleDefinition = {
      match: { field: "extension", op: "eq", value: "mkv" },
      actions: [{ type: "delete", confirm: false } as Action],
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

    fireEvent.click(screen.getByRole("checkbox", { name: /confirm/i }));

    expect(captured).not.toBeNull();
    const action = captured!.actions[0] as Action;
    expect(action.type).toBe("delete");
    if (action.type === "delete") {
      expect(action.confirm).toBe(true);
    }
  });
});
