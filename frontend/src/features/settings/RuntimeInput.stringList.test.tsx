/**
 * v1.10 — RuntimeInput renderer for ``string_list`` fields.
 *
 * Verifies:
 *   1. A ``string_list`` field renders a text input and a
 *      chips preview.
 *   2. The chip preview reflects the current value, lowercased.
 *   3. Typing a comma-separated string emits the string verbatim
 *      via onChange (the backend pre-coerces to list).
 *   4. A list value is accepted and rendered as joined chips.
 *   5. Empty value hides the chips row.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { RuntimeInput } from "./RuntimeInput";

function makeField(value: string | string[]) {
  return {
    key: "preferred_audio_languages",
    label: "Preferred audio languages",
    description: "ISO 639-2 codes",
    category: "dashboard",
    group: "language_preferences",
    type: "string_list" as const,
    default: ["eng"],
    options: null,
    constraints: {},
    impact: "immediate" as const,
    sensitivity: "normal" as const,
    restart_required: false,
    requires_warning: null,
    value,
    is_override: false,
    env_default: ["eng"],
  };
}

describe("RuntimeInput — string_list", () => {
  it("renders chip preview + text input for a list value", () => {
    render(
      <RuntimeInput
        field={makeField(["eng", "fra"])}
        value={["eng", "fra"]}
        onChange={() => {}}
      />,
    );
    const chips = screen.getByTestId("string-list-chips");
    expect(chips.textContent).toContain("eng");
    expect(chips.textContent).toContain("fra");
    const input = screen.getByTestId("string-list-input") as HTMLInputElement;
    expect(input.value).toBe("eng, fra");
  });

  it("accepts a comma-separated string and reflects it in chips", () => {
    render(
      <RuntimeInput
        field={makeField("eng,fra,spa")}
        value={"eng,fra,spa"}
        onChange={() => {}}
      />,
    );
    const chips = screen.getByTestId("string-list-chips");
    // Tokenized + lowercased.
    expect(chips.textContent).toContain("eng");
    expect(chips.textContent).toContain("fra");
    expect(chips.textContent).toContain("spa");
  });

  it("lowercases tokens in the chips preview", () => {
    render(
      <RuntimeInput
        field={makeField("ENG, FRA")}
        value={"ENG, FRA"}
        onChange={() => {}}
      />,
    );
    const chips = screen.getByTestId("string-list-chips");
    expect(chips.textContent).toContain("eng");
    expect(chips.textContent).toContain("fra");
    expect(chips.textContent).not.toContain("ENG");
  });

  it("emits the raw string on change", () => {
    const onChange = vi.fn();
    render(
      <RuntimeInput
        field={makeField(["eng"])}
        value={["eng"]}
        onChange={onChange}
      />,
    );
    const input = screen.getByTestId("string-list-input");
    fireEvent.change(input, { target: { value: "eng, fra" } });
    // We forward the raw string; the backend pre-coerces.
    expect(onChange).toHaveBeenCalledWith("eng, fra");
  });

  it("hides the chips row when there are no tokens", () => {
    render(
      <RuntimeInput
        field={makeField([])}
        value={[]}
        onChange={() => {}}
      />,
    );
    expect(
      screen.queryByTestId("string-list-chips"),
    ).not.toBeInTheDocument();
  });
});
