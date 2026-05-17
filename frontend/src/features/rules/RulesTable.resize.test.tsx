/**
 * Stage 03 — Rules table column resize commits the new width to
 * the rules prefs store.
 *
 * Mirrors ``FilesTable.resize.test.tsx``. The two tables share
 * the ``ResizableHeaderCell`` primitive (Stage 03 extracted it
 * from Stage 02's inline implementation); this test pins that
 * the rules side is wired to the rules-specific prefs store, not
 * the files one.
 */
import { describe, expect, it, beforeEach } from "vitest";
import { render } from "@testing-library/react";

import { RulesTable } from "./RulesTable";
import {
  useRulesPrefs,
  effectiveRulesColumnWidth,
} from "@/stores/rulesPrefsStore";
import { useFilesPrefs } from "@/stores/filesPrefsStore";

function makeQueryMock(rules: unknown[]) {
  return {
    isLoading: false,
    isError: false,
    data: rules,
    error: undefined,
  };
}

function makeRule(overrides: Record<string, unknown> = {}) {
  return {
    id: "r1",
    name: "Sample rule",
    description: "",
    enabled: true,
    priority: 50,
    is_builtin: false,
    last_match_count: 0,
    last_evaluated_at: null,
    created_at: "2025-01-01T00:00:00Z",
    updated_at: "2025-01-01T00:00:00Z",
    definition: {
      match: {
        all: [{ field: "category", op: "eq", value: "media" }],
      },
      actions: [{ type: "set_severity", severity: "info" }],
    },
    ...overrides,
  };
}

describe("RulesTable — Stage 03 column resize", () => {
  beforeEach(() => {
    useRulesPrefs.setState({ columnWidths: {} });
    useFilesPrefs.setState({ columnWidths: {}, perColumnFilters: {} });
    window.localStorage.clear();
  });

  it("commits a new width on pointerup to the rules prefs store", () => {
    const rule = makeRule();
    const noop = () => {};
    const { container } = render(
      <RulesTable
        variant="custom"
        query={makeQueryMock([rule]) as never}
        visibleRules={[rule] as never}
        onEdit={noop}
        onToggle={noop}
        onDuplicate={noop}
        onDelete={noop}
      />,
    );

    const handle = container.querySelector<HTMLSpanElement>(
      'span[aria-label="Adjust priority column width"]',
    );
    expect(handle, "priority column resize handle must render").not.toBeNull();
    if (!handle) return;

    // JSDOM PointerEvent stubs.
    let captured = false;
    handle.setPointerCapture = () => {
      captured = true;
    };
    handle.releasePointerCapture = () => {
      captured = false;
    };
    handle.hasPointerCapture = () => captured;

    function dispatchPointer(
      type: "pointerdown" | "pointermove" | "pointerup",
      clientX: number,
    ) {
      const ev = new Event(type, { bubbles: true, cancelable: true });
      Object.defineProperty(ev, "clientX", { value: clientX });
      Object.defineProperty(ev, "clientY", { value: 0 });
      Object.defineProperty(ev, "pointerId", { value: 1 });
      Object.defineProperty(ev, "button", { value: 0 });
      handle!.dispatchEvent(ev);
    }

    // Start width: 90 px (default for priority). Drag +60 → 150.
    dispatchPointer("pointerdown", 1000);
    dispatchPointer("pointermove", 1060);
    dispatchPointer("pointerup", 1060);

    const stored = useRulesPrefs.getState().columnWidths;
    expect(stored.priority).toBe(150);
    expect(effectiveRulesColumnWidth("priority", stored)).toBe(150);
  });

  it("does not write to the files prefs store", () => {
    // The two stores are intentionally separate. Resizing a
    // rules column should not bleed into Files-table state.
    const rule = makeRule();
    const noop = () => {};
    const { container } = render(
      <RulesTable
        variant="custom"
        query={makeQueryMock([rule]) as never}
        visibleRules={[rule] as never}
        onEdit={noop}
        onToggle={noop}
        onDuplicate={noop}
        onDelete={noop}
      />,
    );
    const handle = container.querySelector<HTMLSpanElement>(
      'span[aria-label="Adjust name column width"]',
    );
    if (!handle) throw new Error("handle missing");
    const h = handle;
    let captured = false;
    h.setPointerCapture = () => {
      captured = true;
    };
    h.releasePointerCapture = () => {
      captured = false;
    };
    h.hasPointerCapture = () => captured;
    function dispatchPointer(
      type: "pointerdown" | "pointermove" | "pointerup",
      clientX: number,
    ) {
      const ev = new Event(type, { bubbles: true, cancelable: true });
      Object.defineProperty(ev, "clientX", { value: clientX });
      Object.defineProperty(ev, "clientY", { value: 0 });
      Object.defineProperty(ev, "pointerId", { value: 2 });
      Object.defineProperty(ev, "button", { value: 0 });
      h.dispatchEvent(ev);
    }
    dispatchPointer("pointerdown", 500);
    dispatchPointer("pointermove", 600);
    dispatchPointer("pointerup", 600);
    expect(useRulesPrefs.getState().columnWidths.name).toBe(460);

    // The files store stays empty (this test doesn't touch
    // the files surface).
    expect(useFilesPrefs.getState().columnWidths).toEqual({});
  });
});
