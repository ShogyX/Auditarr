/**
 * Stage 15 (plan §663) — VisualRuleBuilder value-input uses
 * the library vocabulary for codec / container / extension /
 * tag fields.
 *
 * Pins that when the operator picks ``field=video_codec``, the
 * value-input renders WITH a backing datalist populated from
 * ``GET /media/vocabulary``'s ``video_codecs`` slice.
 *
 * The datalist is the unobtrusive UX: free-text input survives
 * (operators can still author rules for codecs not yet
 * indexed), but the dropdown surface drives them toward the
 * library's actual values.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
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

// Mock the API client. Returns a stable vocabulary payload
// from /media/vocabulary; everything else falls through.
const __mockVocabulary = {
  video_codecs: ["av1", "h264", "hevc"],
  audio_codecs: ["aac", "eac3"],
  containers: ["mkv", "mp4"],
  extensions: ["mkv", "mp4", "nfo"],
  tags: ["plex:1080p", "sonarr:downloaded"],
};

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: vi.fn(async (path: string) => {
      if (path === "/media/vocabulary") return __mockVocabulary;
      return null;
    }),
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
    user: {
      id: "u1",
      role: "admin" as const,
      email: "a@b.c",
      username: "admin",
    },
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
  };
  useAuthStore.getState = () => state;
  return { useAuthStore };
});

import { VisualRuleBuilder } from "@/features/rules/VisualRuleBuilder";
import type {
  RuleDefinition,
  RuleVocabulary,
} from "@/hooks/useRules";

function wrap(child: ReactNode): ReactNode {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
    },
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
    { key: "video_codec", label: "Video codec", type: "string", enum: null },
    { key: "audio_codec", label: "Audio codec", type: "string", enum: null },
    { key: "container", label: "Container", type: "string", enum: null },
    { key: "filename", label: "Filename", type: "string", enum: null },
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
  ],
  rule_flags: {},
};

beforeEach(() => {
  /* noop */
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Stage 15 — VisualRuleBuilder vocabulary-driven datalist", () => {
  it("renders a datalist populated from /media/vocabulary when field=video_codec", async () => {
    const definition: RuleDefinition = {
      match: { field: "video_codec", op: "eq", value: "hevc" },
      actions: [{ type: "set_severity", severity: "warn" }],
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

    // Wait for the vocabulary query to resolve and the
    // datalist to render. Use the stable testid.
    const datalist = await screen.findByTestId(
      "rule-value-datalist-video_codec",
    );
    // The three video codecs from the mocked endpoint.
    const options = Array.from(datalist.querySelectorAll("option"));
    const values = options.map(
      (o) => (o as unknown as { value: string }).value,
    );
    expect(values).toEqual(["av1", "h264", "hevc"]);
  });

  it("the input is wired to the datalist via the list attribute", async () => {
    const definition: RuleDefinition = {
      match: { field: "audio_codec", op: "eq", value: "" },
      actions: [{ type: "set_severity", severity: "warn" }],
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

    const input = await screen.findByTestId(
      "rule-value-input-audio_codec",
    );
    expect(input.getAttribute("list")).toBe("vocab-audio_codec");

    // And the datalist exists with the audio codecs.
    const datalist = screen.getByTestId(
      "rule-value-datalist-audio_codec",
    );
    const options = Array.from(datalist.querySelectorAll("option"));
    expect(options.map((o) => (o as unknown as { value: string }).value)).toEqual(
      ["aac", "eac3"],
    );
  });

  it("free-text typing still propagates via onChange (vocabulary is non-restricting)", async () => {
    const onChange = vi.fn();
    const definition: RuleDefinition = {
      match: { field: "container", op: "eq", value: "mkv" },
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

    const input = (await screen.findByTestId(
      "rule-value-input-container",
    )) as HTMLInputElement;

    // Type a value NOT in the vocabulary (e.g. a future
    // container not yet indexed). The change should still
    // propagate.
    fireEvent.change(input, { target: { value: "webm" } });

    // The onChange call shape contains the new definition;
    // we just need to assert some onChange happened with
    // the new value somewhere in the payload.
    expect(onChange).toHaveBeenCalled();
    const calls = onChange.mock.calls;
    const lastCall = calls[calls.length - 1]?.[0];
    // ``lastCall`` is the new RuleDefinition; the match's
    // value should reflect "webm".
    expect(JSON.stringify(lastCall)).toContain("webm");
  });

  it("does NOT render a datalist for fields without a library slice (e.g. filename)", async () => {
    const definition: RuleDefinition = {
      match: { field: "filename", op: "eq", value: "Movie.mkv" },
      actions: [{ type: "set_severity", severity: "warn" }],
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

    // No datalist for filename; the plain string input
    // renders without the stage-15 hookup.
    expect(
      screen.queryByTestId("rule-value-datalist-filename"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("rule-value-input-filename"),
    ).not.toBeInTheDocument();
  });
});
