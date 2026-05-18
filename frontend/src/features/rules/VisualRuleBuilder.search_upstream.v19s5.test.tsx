/**
 * v1.9 Stage 5.2 — search_upstream rule action UI.
 *
 * The visual builder's action dropdown gains "Search in upstream".
 * Picking it shows two selects (target + integration), where
 * integration is filtered to enabled integrations whose kind
 * matches the chosen target.
 *
 * Pins:
 *   1. Action dropdown includes "Search in upstream" when the
 *      vocabulary surfaces it.
 *   2. With action.type=search_upstream the row renders two
 *      selects (target + integration), with the integration
 *      list filtered to the matching kind.
 *   3. Changing the target select clears the previously-picked
 *      integration_id (different kind, can't reuse).
 *   4. Picking an integration calls onChange with the right
 *      action shape.
 *   5. Empty filter (no enabled integrations of the chosen kind)
 *      shows the "no enabled X integrations" placeholder.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";

const apiGet = vi.fn();

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: (path: string) => apiGet(path),
    post: vi.fn(async () => null),
    put: vi.fn(async () => null),
    patch: vi.fn(async () => null),
    delete: vi.fn(async () => null),
  },
  ApiError: class extends Error {},
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
import type { RuleDefinition, RuleVocabulary } from "@/hooks/useRules";

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

// Minimal vocab — includes search_upstream so the action dropdown
// surfaces it. Mirrors the backend's
// /api/v1/rules/vocabulary response.
const VOCAB: RuleVocabulary = {
  fields: [
    { key: "extension", label: "Extension", type: "string", enum: null },
  ],
  ops: {
    string: ["eq", "ne"],
    numeric: ["eq", "gt"],
    bool: ["eq"],
    array: ["any_of"],
  },
  severities: ["ok", "info", "warn", "high", "error", "crit"],
  actions: [
    {
      type: "set_severity",
      label: "Set severity",
      args_schema: {
        severity: {
          type: "string",
          enum: ["ok", "warn", "crit"],
          required: true,
        },
      },
    },
    {
      type: "search_upstream",
      label: "Search in upstream",
      args_schema: {
        target: {
          type: "string",
          enum: ["sonarr", "radarr", "bazarr"],
          required: true,
        },
        integration_id: {
          type: "string",
          required: true,
          format: "integration_picker",
        },
      },
    },
  ],
  rule_flags: {},
};

const INTEGRATIONS = [
  { id: "snrr-1", name: "Sonarr Prod", kind: "sonarr", enabled: true },
  { id: "snrr-2", name: "Sonarr Dev", kind: "sonarr", enabled: true },
  { id: "snrr-3", name: "Sonarr Off", kind: "sonarr", enabled: false },
  { id: "rdrr-1", name: "Radarr Prod", kind: "radarr", enabled: true },
  { id: "plex-1", name: "Plex", kind: "plex", enabled: true },
];

function setApi(integrations: typeof INTEGRATIONS = INTEGRATIONS) {
  apiGet.mockImplementation(async (path: string) => {
    if (path === "/integrations") return integrations;
    return null;
  });
}

afterEach(() => {
  vi.clearAllMocks();
  apiGet.mockReset();
});

const BASE_DEF: RuleDefinition = {
  match: { field: "extension", op: "eq", value: "mkv" },
  actions: [{ type: "set_severity", severity: "warn" }],
};

describe("v1.9 Stage 5.2 — search_upstream action UI", () => {
  it("offers 'Search in upstream' in the action-type dropdown", () => {
    setApi();
    render(
      wrap(
        <VisualRuleBuilder
          definition={BASE_DEF}
          vocabulary={VOCAB}
          onChange={vi.fn()}
        />,
      ),
    );
    // The action-type select carries every vocab.actions entry as
    // an option. "Search in upstream" must be present.
    const actionTypeSelects = screen.getAllByRole("combobox", {
      name: /action type/i,
    });
    expect(actionTypeSelects[0]!.innerHTML).toContain("Search in upstream");
  });

  it("renders target + integration selects when action.type=search_upstream", async () => {
    setApi();
    const definition: RuleDefinition = {
      match: { field: "extension", op: "eq", value: "mkv" },
      actions: [
        {
          type: "search_upstream",
          target: "sonarr",
          integration_id: "snrr-1",
        },
      ],
    };
    render(
      wrap(
        <VisualRuleBuilder
          definition={definition}
          vocabulary={VOCAB}
          onChange={vi.fn()}
        />,
      ),
    );
    const targetSelect = await screen.findByLabelText(
      /search upstream target/i,
    );
    const integrationSelect = await screen.findByLabelText(
      /search upstream integration/i,
    );
    expect((targetSelect as HTMLSelectElement).value).toBe("sonarr");
    expect((integrationSelect as HTMLSelectElement).value).toBe("snrr-1");
    // Only ENABLED sonarr integrations populate the integration
    // dropdown — Sonarr Off is filtered out, Radarr / Plex too.
    expect(integrationSelect.innerHTML).toContain("Sonarr Prod");
    expect(integrationSelect.innerHTML).toContain("Sonarr Dev");
    expect(integrationSelect.innerHTML).not.toContain("Sonarr Off");
    expect(integrationSelect.innerHTML).not.toContain("Radarr Prod");
    expect(integrationSelect.innerHTML).not.toContain("Plex");
  });

  it("changing the target select clears the previously-picked integration_id", async () => {
    setApi();
    const onChange = vi.fn();
    const definition: RuleDefinition = {
      match: { field: "extension", op: "eq", value: "mkv" },
      actions: [
        {
          type: "search_upstream",
          target: "sonarr",
          integration_id: "snrr-1",
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
    const targetSelect = await screen.findByLabelText(
      /search upstream target/i,
    );
    fireEvent.change(targetSelect, { target: { value: "radarr" } });
    // The mutation: target=radarr, integration_id cleared. The
    // builder also flips top-level shape — actions[0] is our
    // mutated action.
    expect(onChange).toHaveBeenCalled();
    const calls = onChange.mock.calls;
    const lastDef = calls[calls.length - 1]?.[0];
    expect(lastDef.actions[0]).toEqual({
      type: "search_upstream",
      target: "radarr",
      integration_id: "",
    });
  });

  it("picking an integration emits the right action shape", async () => {
    setApi();
    const onChange = vi.fn();
    const definition: RuleDefinition = {
      match: { field: "extension", op: "eq", value: "mkv" },
      actions: [
        {
          type: "search_upstream",
          target: "sonarr",
          integration_id: "",
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
    const integrationSelect = await screen.findByLabelText(
      /search upstream integration/i,
    );
    // Wait for the integrations query to resolve so the <select>
    // actually contains the option we're about to pick (fireEvent
    // is synchronous; without this wait the select's options are
    // still just the empty placeholder).
    await screen.findByRole("option", { name: /Sonarr Dev/i });
    fireEvent.change(integrationSelect, { target: { value: "snrr-2" } });
    expect(onChange).toHaveBeenCalled();
    const calls = onChange.mock.calls;
    const lastDef = calls[calls.length - 1]?.[0];
    expect(lastDef.actions[0]).toEqual({
      type: "search_upstream",
      target: "sonarr",
      integration_id: "snrr-2",
    });
  });

  it("shows a 'no enabled X integrations' placeholder when filter is empty", async () => {
    // Only a plex integration exists — no sonarr/radarr/bazarr.
    setApi([
      { id: "plex-1", name: "Plex", kind: "plex", enabled: true },
    ]);
    const definition: RuleDefinition = {
      match: { field: "extension", op: "eq", value: "mkv" },
      actions: [
        {
          type: "search_upstream",
          target: "sonarr",
          integration_id: "",
        },
      ],
    };
    render(
      wrap(
        <VisualRuleBuilder
          definition={definition}
          vocabulary={VOCAB}
          onChange={vi.fn()}
        />,
      ),
    );
    const integrationSelect = await screen.findByLabelText(
      /search upstream integration/i,
    );
    expect(integrationSelect.innerHTML).toContain(
      "No enabled sonarr integrations",
    );
  });
});
