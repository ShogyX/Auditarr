/**
 * Stage 18 (audit follow-up) — tag-scope chip-input.
 *
 * Pins:
 *   1. ArgInput with format="tag_list" renders the chip input.
 *   2. Selecting a tag from the Select dropdown commits a
 *      string[] to onChange.
 *   3. Removing a chip commits the reduced array.
 *   4. Typing a free-form tag + Enter adds it (operator can pin a
 *      tag that's not in the catalog yet).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
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
import { useState } from "react";

const apiGet = vi.fn();

vi.mock("@/services/apiClient", () => ({
  apiClient: {
    get: (path: string) => apiGet(path),
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

import { ArgInput } from "@/features/automation/scheduleFormShared";

function wrap(child: ReactNode): ReactNode {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return <QueryClientProvider client={qc}>{child}</QueryClientProvider>;
}

beforeEach(() => {
  apiGet.mockReset();
  apiGet.mockImplementation(async (path: string) => {
    if (path === "/tags") return ["4K", "radarr", "sonarr"];
    return null;
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("Stage 18 — TagListField (format: tag_list)", () => {
  it("renders the chip input + Select with catalog tags", async () => {
    render(
      wrap(
        <ArgInput
          argKey="tags"
          spec={{
            type: "array",
            title: "Restrict to tags",
            format: "tag_list",
          }}
          required={false}
          value={[]}
          onChange={() => {}}
        />,
      ),
    );

    await waitFor(() => {
      expect(screen.getByTestId("tag-list-input")).toBeInTheDocument();
    });
    // Wait for the catalog Select to appear (it renders only once
    // useTagsCatalog resolves).
    const select = (await screen.findByLabelText(
      /Add tag to/i,
    )) as HTMLSelectElement;
    const optionLabels = Array.from(select.options).map((o) => o.textContent);
    expect(optionLabels).toContain("4K");
    expect(optionLabels).toContain("sonarr");
  });

  it("selecting a tag commits the array to onChange", async () => {
    const onChange = vi.fn();
    function Wrapper() {
      const [val, setVal] = useState<string[]>([]);
      return (
        <ArgInput
          argKey="tags"
          spec={{ type: "array", title: "Tags", format: "tag_list" }}
          required={false}
          value={val}
          onChange={(next) => {
            setVal(next as string[]);
            onChange(next);
          }}
        />
      );
    }
    render(wrap(<Wrapper />));
    await waitFor(() => {
      expect(screen.getByLabelText(/Add tag to/i)).toBeInTheDocument();
    });
    fireEvent.change(screen.getByLabelText(/Add tag to/i), {
      target: { value: "sonarr" },
    });
    await waitFor(() => {
      expect(onChange).toHaveBeenLastCalledWith(["sonarr"]);
    });
    // The chip rendered + the same tag is no longer in the picker.
    const chip = screen.getByTestId("tag-chip");
    expect(chip.textContent).toContain("sonarr");
  });

  it("removing a chip commits the reduced array", async () => {
    const onChange = vi.fn();
    function Wrapper() {
      const [val, setVal] = useState<string[]>(["sonarr", "4K"]);
      return (
        <ArgInput
          argKey="tags"
          spec={{ type: "array", title: "Tags", format: "tag_list" }}
          required={false}
          value={val}
          onChange={(next) => {
            setVal(next as string[]);
            onChange(next);
          }}
        />
      );
    }
    render(wrap(<Wrapper />));

    await waitFor(() => {
      const editor = screen.getByTestId("tag-list-input");
      expect(within(editor).getAllByTestId("tag-chip")).toHaveLength(2);
    });
    fireEvent.click(
      screen.getByRole("button", { name: /Remove tag sonarr/i }),
    );
    expect(onChange).toHaveBeenLastCalledWith(["4K"]);
  });

  it("typing a custom tag + Enter adds it (even if not in catalog)", async () => {
    const onChange = vi.fn();
    function Wrapper() {
      const [val, setVal] = useState<string[]>([]);
      return (
        <ArgInput
          argKey="tags"
          spec={{ type: "array", title: "Tags", format: "tag_list" }}
          required={false}
          value={val}
          onChange={(next) => {
            setVal(next as string[]);
            onChange(next);
          }}
        />
      );
    }
    render(wrap(<Wrapper />));
    const input = await screen.findByLabelText(/Add custom tag to/i);
    fireEvent.change(input, { target: { value: "experiment-2026" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => {
      expect(onChange).toHaveBeenLastCalledWith(["experiment-2026"]);
    });
  });
});
