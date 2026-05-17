/**
 * Stage 04 — Housekeeping layout rendering.
 *
 * Plan §269: "render the panel with ``categoryFilter='housekeeping'``;
 * assert ``.runtime-fields`` resolves to ``display: grid`` so the
 * cards reflow rather than stacking single-column."
 *
 * Vitest is configured with ``css: false`` (see ``vitest.config.ts``)
 * — the JSDOM renderer doesn't apply the project stylesheet. That
 * means ``getComputedStyle(el).display`` always returns "" for
 * rules sourced from ``components.css``, regardless of what the
 * actual CSS says.
 *
 * The intent of the plan §269 check is to pin the CSS contract:
 * the runtime-fields container must be a grid, not the old flex
 * column. We satisfy that by reading ``components.css`` directly
 * and asserting the rule shape. A complementary structural test
 * confirms the panel renders the right container element so a
 * future CSS regression is caught at the right place.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";

// ESM equivalent of CommonJS __dirname; vitest runs as ESM
// and the global isn't defined.
const __dirname = dirname(fileURLToPath(import.meta.url));
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { RuntimeSettingsPanel } from "./RuntimeSettingsPanel";

// Stub the data hooks so the panel renders deterministically.
// ``importOriginal`` so unmocked exports (e.g. RuntimeField types
// re-exported through here, or the history hook the drawer
// reaches for) keep their real shape.
vi.mock("@/hooks/useRuntimeSettings", async () => {
  const actual: Record<string, unknown> = await vi.importActual(
    "@/hooks/useRuntimeSettings",
  );
  return {
    ...actual,
    useRuntimeSettings: () => ({
      categories: [{ key: "housekeeping", label: "Housekeeping" }],
      groups: [],
      fields: [
        {
          key: "trash_retention_days",
          label: "Trash retention (days)",
          description: "How long to keep deleted files in trash.",
          category: "housekeeping",
          group: null,
          type: "integer",
          default: 30,
          options: null,
          constraints: { ge: 1, le: 365 },
          impact: "next_tick",
          sensitivity: "normal",
          restart_required: false,
          requires_warning: null,
          value: 30,
          is_override: false,
          env_default: 30,
        },
        {
          key: "audit_retention_days",
          label: "Audit retention (days)",
          description: "How long to keep audit events.",
          category: "housekeeping",
          group: null,
          type: "integer",
          default: 90,
          options: null,
          constraints: { ge: 1, le: 730 },
          impact: "next_tick",
          sensitivity: "normal",
          restart_required: false,
          requires_warning: null,
          value: 90,
          is_override: false,
          env_default: 90,
        },
      ],
      isLoading: false,
      isError: false,
      error: undefined,
    }),
    useSetRuntimeOverride: () => ({
      mutate: () => {},
      mutateAsync: async () => undefined,
      isPending: false,
    }),
    useClearRuntimeOverride: () => ({
      mutate: () => {},
      mutateAsync: async () => undefined,
      isPending: false,
    }),
  };
});

function renderPanel() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <RuntimeSettingsPanel categoryFilter="housekeeping" />
    </QueryClientProvider>,
  );
}

describe("RuntimeSettingsPanel — Stage 04 housekeeping layout", () => {
  it("renders the ``.runtime-fields`` container so the CSS rule has a target", () => {
    const { container } = renderPanel();
    const fields = container.querySelector(".runtime-fields");
    expect(fields, ".runtime-fields container must render").not.toBeNull();
  });

  it("the ``.runtime-fields`` CSS rule resolves to ``display: grid`` with auto-fit columns", () => {
    // Read the stylesheet directly — JSDOM doesn't apply it
    // (vitest config sets ``css: false``), so we pin the
    // contract at the source.
    const cssPath = join(
      __dirname,
      "..",
      "..",
      "styles",
      "components.css",
    );
    const css = readFileSync(cssPath, "utf-8");

    // Match the ``.runtime-fields`` rule block. Greedy match up to
    // the first closing brace at column 0 — components.css blocks
    // are single-level.
    const ruleMatch = css.match(/\.runtime-fields\s*\{[^}]*\}/);
    expect(ruleMatch, "expected a .runtime-fields rule block").not.toBeNull();
    const block = ruleMatch![0];

    // The Stage 04 contract: grid, with auto-fit + minmax columns.
    expect(block).toMatch(/display:\s*grid/);
    expect(block).toMatch(/grid-template-columns:[^;]*auto-fit/);
    expect(block).toMatch(/minmax\(/);

    // Inverse: the old ``flex-direction: column`` line must be
    // gone as a live declaration. The Stage 04 edit retained a
    // comment that explains the change ("was ``display: flex;
    // flex-direction: column``"); we strip comments before the
    // check so the comment doesn't false-positive.
    const blockNoComments = block.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(blockNoComments).not.toMatch(/flex-direction:\s*column/);
    expect(blockNoComments).not.toMatch(/display:\s*flex\b/);
  });

  it("renders all housekeeping fields", () => {
    const { container } = renderPanel();
    // Two fields → two .runtime-field cards.
    const cards = container.querySelectorAll(".runtime-field");
    expect(cards.length).toBe(2);
  });
});
