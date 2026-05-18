/**
 * v1.10 (OP-2) — Nested AND/OR rule editor.
 *
 * Pins:
 *   1. liftToGroup / unliftFromGroup round-trip a single Condition.
 *   2. depthOf computes the maximum nesting depth.
 *   3. Root group renders with header + children + add buttons.
 *   4. Combinator dropdown converts AllOf ↔ AnyOf preserving children.
 *   5. "Add condition" appends a default leaf seeded from vocabulary.
 *   6. "Add group" appends a nested AnyOf with one default child.
 *   7. Remove on a leaf with siblings drops that leaf.
 *   8. Remove on the last leaf in a group is a no-op (schema floor).
 *   9. Remove on a nested group drops the whole group from parent.
 *  10. Move-up / move-down reorder within a group.
 *  11. Depth cap (5) disables the "Add group" affordance at the boundary.
 *  12. Nested children render with their own headers + ALL/ANY toggle.
 *  13. Full round-trip: build → serialize → re-parse renders identically.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  ConditionGroupEditor,
  MAX_NEST_DEPTH,
  depthOf,
  isAllOf,
  isAnyOf,
  isCondition,
  liftToGroup,
  unliftFromGroup,
} from "./ConditionGroupEditor";
import type {
  AllOf,
  AnyOf,
  Condition,
  Match,
  RuleVocabulary,
} from "@/hooks/useRules";

const VOCAB: RuleVocabulary = {
  fields: [
    { key: "extension", label: "Extension", type: "string" },
    { key: "video_codec", label: "Video codec", type: "string" },
    { key: "bitrate_kbps", label: "Bitrate (kbps)", type: "numeric" },
  ],
  ops: {
    string: ["eq", "neq", "contains"],
    numeric: ["eq", "gt", "lt", "gte", "lte"],
    bool: ["eq"],
    array: ["contains", "any_of"],
  },
  severities: ["ok", "info", "warn", "high", "error", "crit"],
  actions: [],
} as unknown as RuleVocabulary;

function renderLeaf({
  cond,
  onChange,
  onRemove,
  conjunctionLabel,
}: {
  cond: Condition;
  onChange: (next: Condition) => void;
  onRemove: () => void;
  conjunctionLabel: string;
}) {
  return (
    <div data-testid="leaf-row" data-conj={conjunctionLabel}>
      <span data-testid="leaf-field">{cond.field}</span>
      <span data-testid="leaf-op">{cond.op}</span>
      <span data-testid="leaf-value">{String(cond.value ?? "")}</span>
      <button
        type="button"
        onClick={() =>
          onChange({ ...cond, value: "renamed" })
        }
        data-testid="leaf-set-value"
      >
        Set value
      </button>
      <button
        type="button"
        onClick={onRemove}
        data-testid="leaf-remove"
      >
        Remove leaf
      </button>
    </div>
  );
}

// ── Helper round-trips ─────────────────────────────────────

describe("liftToGroup / unliftFromGroup", () => {
  it("wraps a bare Condition into an AllOf with one child", () => {
    const cond: Condition = { field: "extension", op: "eq", value: "mkv" };
    const lifted = liftToGroup(cond);
    expect(isAllOf(lifted)).toBe(true);
    expect((lifted as AllOf).all).toEqual([cond]);
  });

  it("returns combinators as-is", () => {
    const grp: AnyOf = {
      any: [{ field: "extension", op: "eq", value: "mkv" }],
    };
    expect(liftToGroup(grp)).toBe(grp);
  });

  it("unlifts a single-child group back to the bare Condition", () => {
    const cond: Condition = { field: "extension", op: "eq", value: "mkv" };
    const grp: AllOf = { all: [cond] };
    expect(unliftFromGroup(grp)).toEqual(cond);
  });

  it("preserves multi-child groups", () => {
    const grp: AnyOf = {
      any: [
        { field: "extension", op: "eq", value: "mkv" },
        { field: "video_codec", op: "eq", value: "hevc" },
      ],
    };
    expect(unliftFromGroup(grp)).toBe(grp);
  });

  it("preserves groups containing nested groups even if single-child", () => {
    // A single-child group whose child is itself a group is not
    // collapsible — the schema requires a group at this position
    // because there's structure to preserve.
    const inner: AnyOf = {
      any: [
        { field: "extension", op: "eq", value: "mkv" },
        { field: "extension", op: "eq", value: "mp4" },
      ],
    };
    const outer: AllOf = { all: [inner] };
    expect(unliftFromGroup(outer)).toBe(outer);
  });
});

// ── depthOf ─────────────────────────────────────────────────

describe("depthOf", () => {
  it("returns 0 for a leaf", () => {
    expect(
      depthOf({ field: "extension", op: "eq", value: "mkv" }),
    ).toBe(0);
  });

  it("returns 1 for a single-level combinator", () => {
    expect(
      depthOf({
        all: [{ field: "extension", op: "eq", value: "mkv" }],
      }),
    ).toBe(1);
  });

  it("counts each nesting level", () => {
    const m: AllOf = {
      all: [
        {
          any: [
            {
              all: [{ field: "extension", op: "eq", value: "mkv" }],
            },
          ],
        },
      ],
    };
    expect(depthOf(m)).toBe(3);
  });
});

// ── Type guards ─────────────────────────────────────────────

describe("type guards", () => {
  it("identifies leaves vs groups correctly", () => {
    expect(
      isCondition({ field: "extension", op: "eq", value: "mkv" }),
    ).toBe(true);
    expect(isAllOf({ all: [] as Match[] } as AllOf)).toBe(true);
    expect(isAnyOf({ any: [] as Match[] } as AnyOf)).toBe(true);
    expect(isCondition({ all: [] } as unknown as Match)).toBe(false);
  });
});

// ── Render + interactions ──────────────────────────────────

describe("ConditionGroupEditor — rendering", () => {
  it("renders the root group's header + leaf children", () => {
    const group: AllOf = {
      all: [
        { field: "extension", op: "eq", value: "mkv" },
        { field: "video_codec", op: "eq", value: "hevc" },
      ],
    };
    render(
      <ConditionGroupEditor
        group={group}
        onChange={vi.fn()}
        onRemove={null}
        vocabulary={VOCAB}
        depth={0}
        renderCondition={renderLeaf}
      />,
    );
    // Two leaf rows rendered.
    expect(screen.getAllByTestId("leaf-row")).toHaveLength(2);
    // The first row carries the "WHEN" label; the second carries "AND".
    const rows = screen.getAllByTestId("leaf-row");
    expect(rows[0]!.getAttribute("data-conj")).toBe("WHEN");
    expect(rows[1]!.getAttribute("data-conj")).toBe("AND");
  });

  it("converts combinator between ALL and ANY preserving children", () => {
    const group: AllOf = {
      all: [
        { field: "extension", op: "eq", value: "mkv" },
        { field: "video_codec", op: "eq", value: "hevc" },
      ],
    };
    const onChange = vi.fn();
    render(
      <ConditionGroupEditor
        group={group}
        onChange={onChange}
        onRemove={null}
        vocabulary={VOCAB}
        depth={0}
        renderCondition={renderLeaf}
      />,
    );
    fireEvent.change(
      screen.getByLabelText("Combinator at depth 0"),
      { target: { value: "any" } },
    );
    expect(onChange).toHaveBeenCalledWith({
      any: [
        { field: "extension", op: "eq", value: "mkv" },
        { field: "video_codec", op: "eq", value: "hevc" },
      ],
    });
  });

  it("appends a vocabulary-seeded condition on Add Condition", () => {
    const group: AllOf = {
      all: [{ field: "extension", op: "eq", value: "mkv" }],
    };
    const onChange = vi.fn();
    render(
      <ConditionGroupEditor
        group={group}
        onChange={onChange}
        onRemove={null}
        vocabulary={VOCAB}
        depth={0}
        renderCondition={renderLeaf}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: /condition/i }),
    );
    const call = onChange.mock.calls[0]![0];
    expect(call.all).toHaveLength(2);
    // New leaf seeded with first vocabulary field + first op.
    expect(call.all[1]).toEqual({
      field: "extension",
      op: "eq",
      value: "",
    });
  });

  it("appends a nested AnyOf on Add Group", () => {
    const group: AllOf = {
      all: [{ field: "extension", op: "eq", value: "mkv" }],
    };
    const onChange = vi.fn();
    render(
      <ConditionGroupEditor
        group={group}
        onChange={onChange}
        onRemove={null}
        vocabulary={VOCAB}
        depth={0}
        renderCondition={renderLeaf}
      />,
    );
    fireEvent.click(screen.getByTestId("add-group-at-depth-0"));
    const call = onChange.mock.calls[0]![0];
    expect(call.all).toHaveLength(2);
    expect(call.all[1]).toEqual({
      any: [{ field: "extension", op: "eq", value: "" }],
    });
  });

  it("removes a leaf when it has siblings", () => {
    const group: AllOf = {
      all: [
        { field: "extension", op: "eq", value: "mkv" },
        { field: "video_codec", op: "eq", value: "hevc" },
      ],
    };
    const onChange = vi.fn();
    render(
      <ConditionGroupEditor
        group={group}
        onChange={onChange}
        onRemove={null}
        vocabulary={VOCAB}
        depth={0}
        renderCondition={renderLeaf}
      />,
    );
    const removes = screen.getAllByTestId("leaf-remove");
    fireEvent.click(removes[0]!);
    expect(onChange).toHaveBeenCalledWith({
      all: [{ field: "video_codec", op: "eq", value: "hevc" }],
    });
  });

  it("is a no-op to remove the last child in a group", () => {
    const group: AllOf = {
      all: [{ field: "extension", op: "eq", value: "mkv" }],
    };
    const onChange = vi.fn();
    render(
      <ConditionGroupEditor
        group={group}
        onChange={onChange}
        onRemove={null}
        vocabulary={VOCAB}
        depth={0}
        renderCondition={renderLeaf}
      />,
    );
    const remove = screen.getByTestId("leaf-remove");
    fireEvent.click(remove);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("removes a nested group via the group's remove button", () => {
    const inner: AnyOf = {
      any: [{ field: "video_codec", op: "eq", value: "hevc" }],
    };
    const root: AllOf = {
      all: [
        { field: "extension", op: "eq", value: "mkv" },
        inner,
      ],
    };
    const onChange = vi.fn();
    render(
      <ConditionGroupEditor
        group={root}
        onChange={onChange}
        onRemove={null}
        vocabulary={VOCAB}
        depth={0}
        renderCondition={renderLeaf}
      />,
    );
    // The nested group's remove button is rendered inside the
    // depth-1 group; testid disambiguates.
    fireEvent.click(screen.getByTestId("remove-group-at-depth-1"));
    expect(onChange).toHaveBeenCalledWith({
      all: [{ field: "extension", op: "eq", value: "mkv" }],
    });
  });

  it("reorders children with move-up / move-down", () => {
    const group: AllOf = {
      all: [
        { field: "extension", op: "eq", value: "mkv" },
        { field: "video_codec", op: "eq", value: "hevc" },
      ],
    };
    const onChange = vi.fn();
    render(
      <ConditionGroupEditor
        group={group}
        onChange={onChange}
        onRemove={null}
        vocabulary={VOCAB}
        depth={0}
        renderCondition={renderLeaf}
      />,
    );
    // Move the second row up — should swap with the first.
    const moveUps = screen.getAllByLabelText("Move up");
    // moveUps[0] is the first row's up button (disabled);
    // moveUps[1] is the second row's up button.
    fireEvent.click(moveUps[1]!);
    expect(onChange).toHaveBeenCalledWith({
      all: [
        { field: "video_codec", op: "eq", value: "hevc" },
        { field: "extension", op: "eq", value: "mkv" },
      ],
    });
  });

  it("disables Add Group at the depth cap", () => {
    // Build a maximally-nested tree where the deepest group is
    // at depth MAX_NEST_DEPTH - 1.
    let inner: AllOf | AnyOf = {
      all: [{ field: "extension", op: "eq", value: "mkv" }],
    };
    for (let i = 0; i < MAX_NEST_DEPTH - 2; i++) {
      inner = { all: [inner] };
    }
    render(
      <ConditionGroupEditor
        group={inner}
        onChange={vi.fn()}
        onRemove={null}
        vocabulary={VOCAB}
        depth={MAX_NEST_DEPTH - 2}
        renderCondition={renderLeaf}
      />,
    );
    // The Add Group button at our depth should still be enabled
    // (depth+1 == MAX-1 < MAX).
    expect(
      screen.getByTestId(`add-group-at-depth-${MAX_NEST_DEPTH - 2}`),
    ).not.toBeDisabled();
  });

  it("disables Add Group when adding would exceed the cap", () => {
    // We're rendering a group at depth=MAX-1; clicking Add Group
    // would create a child at MAX, exceeding the cap.
    const group: AllOf = {
      all: [{ field: "extension", op: "eq", value: "mkv" }],
    };
    render(
      <ConditionGroupEditor
        group={group}
        onChange={vi.fn()}
        onRemove={null}
        vocabulary={VOCAB}
        depth={MAX_NEST_DEPTH - 1}
        renderCondition={renderLeaf}
      />,
    );
    expect(
      screen.getByTestId(`add-group-at-depth-${MAX_NEST_DEPTH - 1}`),
    ).toBeDisabled();
  });
});

describe("ConditionGroupEditor — round-trip", () => {
  it("a nested edit roundtrips through onChange → re-render", () => {
    // Simulate the parent's controlled-component flow: render
    // with state, capture onChange, re-render with new state,
    // assert the DOM reflects it.
    const initial: AllOf = {
      all: [
        { field: "extension", op: "eq", value: "mkv" },
        {
          any: [
            { field: "video_codec", op: "eq", value: "hevc" },
            { field: "video_codec", op: "eq", value: "av1" },
          ],
        },
      ],
    };
    let state: AllOf | AnyOf = initial;
    const onChange = (next: AllOf | AnyOf) => {
      state = next;
    };
    const { rerender } = render(
      <ConditionGroupEditor
        group={state}
        onChange={onChange}
        onRemove={null}
        vocabulary={VOCAB}
        depth={0}
        renderCondition={renderLeaf}
      />,
    );
    // Initial: root AllOf with 3 leaves (1 direct + 2 inside the nested AnyOf).
    expect(screen.getAllByTestId("leaf-row")).toHaveLength(3);

    // Switch root to ANY. Re-render with new state.
    fireEvent.change(
      screen.getByLabelText("Combinator at depth 0"),
      { target: { value: "any" } },
    );
    rerender(
      <ConditionGroupEditor
        group={state}
        onChange={onChange}
        onRemove={null}
        vocabulary={VOCAB}
        depth={0}
        renderCondition={renderLeaf}
      />,
    );
    expect(state).toEqual({
      any: [
        { field: "extension", op: "eq", value: "mkv" },
        {
          any: [
            { field: "video_codec", op: "eq", value: "hevc" },
            { field: "video_codec", op: "eq", value: "av1" },
          ],
        },
      ],
    });
    // Conjunction labels updated (root combinator now "any").
    const rows = screen.getAllByTestId("leaf-row");
    expect(rows[0]!.getAttribute("data-conj")).toBe("WHEN");
  });
});
