/**
 * Stage 11 (v1.7) → v1.9 Stage 7.1 — IntegrationDynamicInput array fields.
 *
 * Stage 11 originally rendered ``type: "array"`` fields as a
 * textarea (one entry per line). v1.9 Stage 7.1 swapped the
 * textarea for a structured editor:
 *
 *   - ``items.type === "string"``        → StringChipEditor
 *   - ``items.type === "object"`` w/from/to → PathMappingEditor
 *   - anything else                       → legacy textarea
 *
 * These tests pin both the dispatch logic AND the chip editor
 * basic interaction (add / remove / Enter key). The deeper
 * tests for each editor live in their own files.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { IntegrationDynamicInput } from "@/features/integrations/IntegrationDynamicInput";

describe("IntegrationDynamicInput array field — chip editor (v1.9 Stage 7.1)", () => {
  it("renders the string chip editor for items.type=string", () => {
    render(
      <IntegrationDynamicInput
        meta={{ type: "array", items: { type: "string" } }}
        value={[]}
        onChange={() => {}}
      />,
    );
    expect(screen.getByTestId("string-chip-editor")).toBeInTheDocument();
  });

  it("renders the path-mapping editor for items.type=object with from/to", () => {
    render(
      <IntegrationDynamicInput
        meta={{
          type: "array",
          items: {
            type: "object",
            properties: { from: {}, to: {} },
          },
        } as never}
        value={[]}
        onChange={() => {}}
      />,
    );
    expect(
      screen.getByTestId("path-mapping-editor"),
    ).toBeInTheDocument();
  });

  it("renders existing values as chips for string arrays", () => {
    render(
      <IntegrationDynamicInput
        meta={{ type: "array", items: { type: "string" } }}
        value={["192.168.1.0/24", "sonarr.local", "10.0.0.5"]}
        onChange={() => {}}
      />,
    );
    expect(screen.getByText("192.168.1.0/24")).toBeInTheDocument();
    expect(screen.getByText("sonarr.local")).toBeInTheDocument();
    expect(screen.getByText("10.0.0.5")).toBeInTheDocument();
  });

  it("commits a new entry on Enter and clears the input", () => {
    function Wrapper({
      onChange,
    }: {
      onChange: (v: unknown) => void;
    }) {
      const [value, setValue] = useState<unknown>([]);
      return (
        <IntegrationDynamicInput
          meta={{ type: "array", items: { type: "string" } }}
          value={value}
          onChange={(v) => {
            setValue(v);
            onChange(v);
          }}
        />
      );
    }
    const onChange = vi.fn();
    render(<Wrapper onChange={onChange} />);
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "10.0.0.5" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenLastCalledWith(["10.0.0.5"]);
    expect((input as HTMLInputElement).value).toBe("");
  });
});
