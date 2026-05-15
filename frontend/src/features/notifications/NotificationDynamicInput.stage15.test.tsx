/**
 * Stage 15 (audit follow-up) — webhook headers KV editor +
 * dialog renders the new schema fields.
 *
 * Pins:
 *   1. NotificationDynamicInput renders a key/value editor for an
 *      ``object``-typed field (the webhook headers map).
 *   2. Adding a header commits a Record<string,string> to onChange.
 *   3. Removing a header commits the updated map.
 *   4. ``method`` enum field renders as a select with POST + PUT.
 */

import { fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { NotificationDynamicInput } from "@/features/notifications/NotificationDynamicInput";

describe("Stage 15 — webhook config form", () => {
  it("renders a KV editor for an object-typed field", () => {
    render(
      <NotificationDynamicInput
        meta={{ type: "object" }}
        value={{ "X-Tenant": "alpha", Authorization: "Bearer xyz" }}
        onChange={() => {}}
      />,
    );
    const editor = screen.getByTestId("object-kv-input");
    // Two rows of (name, value) inputs.
    const nameInputs = within(editor).getAllByPlaceholderText(/header name/i);
    const valueInputs = within(editor).getAllByPlaceholderText(/value/i);
    expect(nameInputs).toHaveLength(2);
    expect(valueInputs).toHaveLength(2);
    expect((nameInputs[0] as HTMLInputElement).value).toBe("X-Tenant");
    expect((valueInputs[0] as HTMLInputElement).value).toBe("alpha");
  });

  it("adding a header commits an updated map to onChange", () => {
    const onChange = vi.fn();
    function Wrapper() {
      // Drive the component as a controlled child so onChange can
      // update value — mirroring how the real dialog uses it.
      const [val, setVal] = useState<Record<string, string>>({});
      return (
        <NotificationDynamicInput
          meta={{ type: "object" }}
          value={val}
          onChange={(next) => {
            setVal(next as Record<string, string>);
            onChange(next);
          }}
        />
      );
    }
    render(<Wrapper />);
    // Initial state: no rows rendered.
    expect(screen.queryAllByPlaceholderText(/header name/i)).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: /add header/i }));
    // After the add click the empty row is rendered. The empty
    // pair is stripped by the commit() helper (no key), so the
    // onChange map is still empty at this point.
    const nameInput = screen.getByPlaceholderText(/header name/i);
    fireEvent.change(nameInput, { target: { value: "X-API-Key" } });
    // After typing a key, onChange fires with the new map.
    expect(onChange).toHaveBeenLastCalledWith({ "X-API-Key": "" });
  });

  it("removing a header commits the updated map", () => {
    const onChange = vi.fn();
    render(
      <NotificationDynamicInput
        meta={{ type: "object" }}
        value={{ A: "1", B: "2" }}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /remove header A/i }));
    expect(onChange).toHaveBeenLastCalledWith({ B: "2" });
  });

  it("renders the method field as a select with POST and PUT", () => {
    render(
      <NotificationDynamicInput
        meta={{ type: "string", enum: ["POST", "PUT"] }}
        value="POST"
        onChange={() => {}}
      />,
    );
    // The schema-driven enum branch picks the Select.
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => o.value);
    expect(options).toEqual(["POST", "PUT"]);
    expect(select.value).toBe("POST");
  });
});
