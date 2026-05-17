/**
 * Stage 11 (v1.7) — IntegrationDynamicInput array support.
 *
 * Plan §549 wants the source_whitelist field surfaced in the
 * Integrations edit dialog. The dialog is schema-driven via
 * IntegrationDynamicInput; the array-type fragment is the
 * piece that previously didn't render at all.
 *
 * Pins:
 *   1. ``type: "array"`` field renders a textarea.
 *   2. Initial value (a list of strings) appears as one
 *      entry per line.
 *   3. Editing the textarea commits a trimmed, non-empty
 *      list of strings to onChange.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { IntegrationDynamicInput } from "@/features/integrations/IntegrationDynamicInput";

describe("Stage 11 — IntegrationDynamicInput array field", () => {
  it("renders a textarea for array-typed fields", () => {
    render(
      <IntegrationDynamicInput
        meta={{ type: "array", items: { type: "string" } }}
        value={[]}
        onChange={() => {}}
      />,
    );
    expect(
      screen.getByTestId("integration-array-input"),
    ).toBeInTheDocument();
  });

  it("renders the initial list as newline-separated lines", () => {
    render(
      <IntegrationDynamicInput
        meta={{ type: "array", items: { type: "string" } }}
        value={["192.168.1.0/24", "sonarr.local", "10.0.0.5"]}
        onChange={() => {}}
      />,
    );
    const textarea = screen.getByTestId(
      "integration-array-input",
    ) as HTMLTextAreaElement;
    expect(textarea.value).toBe(
      "192.168.1.0/24\nsonarr.local\n10.0.0.5",
    );
  });

  it("commits a trimmed, non-empty list of strings on edit", () => {
    const onChange = vi.fn();
    function Wrapper() {
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
    render(<Wrapper />);
    const textarea = screen.getByTestId("integration-array-input");
    fireEvent.change(textarea, {
      // Include leading whitespace and a blank line — these
      // should be normalized away.
      target: {
        value: "  192.168.1.10  \n\n10.0.0.5\n",
      },
    });
    // The final onChange should carry the trimmed, non-empty
    // list.
    const last = onChange.mock.calls.at(-1)?.[0];
    expect(last).toEqual(["192.168.1.10", "10.0.0.5"]);
  });
});
