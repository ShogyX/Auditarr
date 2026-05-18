/**
 * v1.10 (OP-2) — Nested AND/OR rule editor.
 *
 * Renders a ``Match`` tree as a recursive set of group cards.
 * Each group:
 *   - has a header with an AND/OR toggle, an "Add condition"
 *     button, an "Add group" button, and (for nested groups) a
 *     remove-group button.
 *   - has a body listing its children. Children are either leaf
 *     ConditionRow elements or nested ConditionGroupEditor
 *     elements.
 *
 * The component is stateless. It reads the current ``Match``
 * shape from props and emits the new shape via ``onChange``.
 * The parent (VisualRuleBuilder) owns the authoritative state.
 *
 * Depth cap: 5 nesting levels. Past depth=5, the "Add group"
 * affordance disables with a tooltip. Operators wanting deeper
 * trees can hand-edit the JSON tab; the cap protects the
 * visual surface from runaway complexity.
 *
 * Empty-group safety: removing the last child of a group is
 * blocked. The schema requires at least one child per
 * combinator. A "remove this group" button on nested groups
 * lets the operator collapse a group entirely; that path also
 * preserves the schema's "at least one child" guarantee by
 * promoting the group's single remaining child to the parent
 * slot (or refusing if the group has more than one child —
 * the operator must remove children explicitly first).
 *
 * v1 scope-cut decisions documented in module header above.
 */

import { useCallback, type ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";
import type {
  AllOf,
  AnyOf,
  Condition,
  Match,
  RuleVocabulary,
  RuleVocabularyField,
} from "@/hooks/useRules";

// ── Depth cap ───────────────────────────────────────────────
// Past 5 levels the visual surface stops being useful — the
// indentation chews horizontal space and operators lose track
// of which group they're editing. Anything past 5 hand-edits
// JSON.
export const MAX_NEST_DEPTH = 5;

// ── Type guards (duplicated from VisualRuleBuilder to avoid
//    coupling — the source-of-truth shapes are the exported
//    interfaces in hooks/useRules.ts). ─────────────────────────
export function isCondition(m: Match): m is Condition {
  return (m as Condition).field !== undefined;
}
export function isAllOf(m: Match): m is AllOf {
  return Array.isArray((m as AllOf).all);
}
export function isAnyOf(m: Match): m is AnyOf {
  return Array.isArray((m as AnyOf).any);
}

/** Return the children array of a combinator. Caller knows it's
 *  one or the other (it's not a leaf). */
function childrenOf(group: AllOf | AnyOf): Match[] {
  return isAllOf(group) ? group.all : (group as AnyOf).any;
}

/** Rebuild a combinator with a new children list. Keeps the
 *  current "all" vs "any" shape. */
function withChildren(group: AllOf | AnyOf, next: Match[]): AllOf | AnyOf {
  return isAllOf(group) ? { all: next } : { any: next };
}

/** Make a leaf condition seeded from vocabulary defaults. */
export function makeDefaultCondition(
  vocabulary: RuleVocabulary,
): Condition {
  const firstField = vocabulary.fields[0];
  if (!firstField) {
    // Hard guard for the fresh-install case where vocabulary is
    // empty. The editor shouldn't be reachable in that state
    // (the page renders a loading skeleton instead), but if it
    // is, return a structurally-valid stub the operator can
    // edit. The field key will fail backend validation until
    // the operator picks a real field; that surface is the right
    // place for that error.
    return { field: "extension", op: "eq", value: "" };
  }
  const firstOp = vocabulary.ops[firstField.type]?.[0] ?? "eq";
  return {
    field: firstField.key,
    op: firstOp,
    value: defaultValueFor(firstField),
  };
}

function defaultValueFor(f: RuleVocabularyField): unknown {
  if (f.type === "numeric") return 0;
  if (f.type === "bool") return false;
  if (f.type === "array") return [];
  if (f.enum && f.enum.length > 0) return f.enum[0];
  return "";
}

// ── ConditionGroupEditor ────────────────────────────────────

export interface ConditionGroupEditorProps {
  /** Current group shape. The root caller normally wraps a
   *  Condition into an AllOf with a single child before
   *  rendering; this editor itself only handles group shapes
   *  (AllOf / AnyOf) — leaves render as direct ConditionRow
   *  children. */
  group: AllOf | AnyOf;
  /** Notify of any structural change. */
  onChange: (next: AllOf | AnyOf) => void;
  /** Remove this group from the parent's child list. ``null``
   *  on the root group (which cannot be removed). */
  onRemove: (() => void) | null;
  vocabulary: RuleVocabulary;
  /** 0 for the root group. Used for indentation + depth-cap
   *  check on Add Group. */
  depth: number;
  /** Renders a leaf condition. Hoisted so this component
   *  doesn't depend on the existing ConditionRow's exact
   *  contract (avoids the circular import between
   *  VisualRuleBuilder and this file). Caller passes the
   *  upstream ConditionRow as the renderer. */
  renderCondition: (props: {
    cond: Condition;
    onChange: (next: Condition) => void;
    onRemove: () => void;
    /** Display-time hint for the leading-conjunction label.
     *  ``"WHEN"`` on the very first child of the very first
     *  group, ``"AND"`` / ``"OR"`` otherwise. */
    conjunctionLabel: string;
  }) => ReactNode;
  /** True when this is the first child of the root group at
   *  depth 0. Drives the "WHEN" vs "AND/OR" label on the
   *  leading row. */
  isRootFirstChild?: boolean;
}

export function ConditionGroupEditor({
  group,
  onChange,
  onRemove,
  vocabulary,
  depth,
  renderCondition,
  isRootFirstChild = true,
}: ConditionGroupEditorProps) {
  const combinator: "all" | "any" = isAllOf(group) ? "all" : "any";
  const children = childrenOf(group);

  // ── Mutations ────────────────────────────────────────────
  const setCombinator = useCallback(
    (next: "all" | "any") => {
      // Convert AllOf ↔ AnyOf preserving children.
      onChange(
        next === "all"
          ? { all: [...children] }
          : { any: [...children] },
      );
    },
    [children, onChange],
  );

  const replaceChild = useCallback(
    (idx: number, next: Match) => {
      const arr = children.map((c, i) => (i === idx ? next : c));
      onChange(withChildren(group, arr));
    },
    [children, group, onChange],
  );

  const removeChild = useCallback(
    (idx: number) => {
      if (children.length <= 1) {
        // Schema requires ≥1 child; refuse the operation. UI
        // disables the remove affordance on the last child too,
        // but the guard here makes the rule explicit.
        return;
      }
      onChange(withChildren(group, children.filter((_, i) => i !== idx)));
    },
    [children, group, onChange],
  );

  const moveChild = useCallback(
    (idx: number, delta: -1 | 1) => {
      const target = idx + delta;
      if (target < 0 || target >= children.length) return;
      const arr = [...children];
      [arr[idx], arr[target]] = [arr[target]!, arr[idx]!];
      onChange(withChildren(group, arr));
    },
    [children, group, onChange],
  );

  const addCondition = useCallback(() => {
    onChange(
      withChildren(group, [...children, makeDefaultCondition(vocabulary)]),
    );
  }, [children, group, onChange, vocabulary]);

  const addGroup = useCallback(() => {
    if (depth + 1 >= MAX_NEST_DEPTH) return;
    // New group seeded as ``any`` (OR), with one default
    // condition child. Operators reach for nested groups when
    // they want an OR-branch under an AND-parent; defaulting
    // to OR matches that intent in the common case.
    const newGroup: AnyOf = {
      any: [makeDefaultCondition(vocabulary)],
    };
    onChange(withChildren(group, [...children, newGroup]));
  }, [children, depth, group, onChange, vocabulary]);

  // ── Render ───────────────────────────────────────────────
  const atMaxDepth = depth + 1 >= MAX_NEST_DEPTH;

  return (
    <div
      className={cn(
        "rounded-md border border-border bg-surface-2/40",
        depth > 0 && "pl-2",
      )}
      data-testid={`condition-group-${depth}`}
      data-combinator={combinator}
    >
      {/* Group header */}
      <div className="flex items-center justify-between gap-2 px-2 py-1.5 border-b border-border bg-surface-2/70">
        <div className="flex items-center gap-2">
          <span className="text-[10.5px] uppercase tracking-[0.06em] text-muted-2 font-semibold">
            {depth === 0 ? "Match" : "Group"}
          </span>
          <select
            aria-label={`Combinator at depth ${depth}`}
            value={combinator}
            onChange={(e) =>
              setCombinator(e.target.value as "all" | "any")
            }
            className={cn(
              "h-6 px-1.5 text-[10.5px] font-mono font-semibold tracking-[0.04em]",
              "bg-surface border border-border rounded",
              "text-text",
              "focus:outline-none focus:ring-2 focus:ring-accent",
            )}
          >
            <option value="all">ALL of</option>
            <option value="any">ANY of</option>
          </select>
          <span className="text-[11px] text-muted-2">
            {combinator === "all"
              ? "every condition must match"
              : "at least one condition must match"}
          </span>
        </div>
        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant="ghost"
            onClick={addCondition}
            title="Add a condition to this group"
          >
            <Icon name="plus" size={12} />
            <span className="ml-1 text-[11px]">Condition</span>
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={addGroup}
            disabled={atMaxDepth}
            title={
              atMaxDepth
                ? `Maximum nesting depth (${MAX_NEST_DEPTH}) reached. Edit JSON mode for deeper trees.`
                : "Add a nested group"
            }
            data-testid={`add-group-at-depth-${depth}`}
          >
            <Icon name="plus" size={12} />
            <span className="ml-1 text-[11px]">Group</span>
          </Button>
          {onRemove ? (
            <Button
              size="sm"
              variant="ghost"
              onClick={onRemove}
              title="Remove this group"
              data-testid={`remove-group-at-depth-${depth}`}
            >
              <Icon name="trash" size={12} />
            </Button>
          ) : null}
        </div>
      </div>

      {/* Children */}
      <div className="flex flex-col gap-2 p-2">
        {children.map((child, idx) => {
          const isFirst = idx === 0;
          const conjunctionLabel = isRootFirstChild && isFirst
            ? "WHEN"
            : combinator === "all"
              ? "AND"
              : "OR";
          const canRemove = children.length > 1;
          const canMoveUp = idx > 0;
          const canMoveDown = idx < children.length - 1;

          if (isCondition(child)) {
            return (
              <div
                key={idx}
                className="flex items-start gap-1"
                data-testid={`condition-child-${depth}-${idx}`}
              >
                <div className="flex-1 min-w-0">
                  {renderCondition({
                    cond: child,
                    onChange: (next) => replaceChild(idx, next),
                    onRemove: canRemove
                      ? () => removeChild(idx)
                      : () => {
                          // Last child guard — no-op.
                        },
                    conjunctionLabel,
                  })}
                </div>
                <ReorderControls
                  canMoveUp={canMoveUp}
                  canMoveDown={canMoveDown}
                  onUp={() => moveChild(idx, -1)}
                  onDown={() => moveChild(idx, 1)}
                />
              </div>
            );
          }

          // Nested group child.
          return (
            <div
              key={idx}
              className="flex items-start gap-1"
              data-testid={`group-child-${depth}-${idx}`}
            >
              <div className="flex-1 min-w-0">
                <ConditionGroupEditor
                  group={child as AllOf | AnyOf}
                  onChange={(next) => replaceChild(idx, next)}
                  onRemove={
                    canRemove ? () => removeChild(idx) : null
                  }
                  vocabulary={vocabulary}
                  depth={depth + 1}
                  renderCondition={renderCondition}
                  // Nested children never get the "WHEN" label
                  // — that's reserved for the very first leaf
                  // at the very top.
                  isRootFirstChild={false}
                />
              </div>
              <ReorderControls
                canMoveUp={canMoveUp}
                canMoveDown={canMoveDown}
                onUp={() => moveChild(idx, -1)}
                onDown={() => moveChild(idx, 1)}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ReorderControls({
  canMoveUp,
  canMoveDown,
  onUp,
  onDown,
}: {
  canMoveUp: boolean;
  canMoveDown: boolean;
  onUp: () => void;
  onDown: () => void;
}) {
  return (
    <div className="flex flex-col mt-1.5">
      <button
        type="button"
        onClick={onUp}
        disabled={!canMoveUp}
        aria-label="Move up"
        title="Move up"
        className={cn(
          "h-4 w-5 inline-flex items-center justify-center text-muted-2 hover:text-text",
          !canMoveUp && "opacity-30 cursor-not-allowed",
        )}
      >
        <Icon name="chev_up" size={10} />
      </button>
      <button
        type="button"
        onClick={onDown}
        disabled={!canMoveDown}
        aria-label="Move down"
        title="Move down"
        className={cn(
          "h-4 w-5 inline-flex items-center justify-center text-muted-2 hover:text-text",
          !canMoveDown && "opacity-30 cursor-not-allowed",
        )}
      >
        <Icon name="chev_down" size={10} />
      </button>
    </div>
  );
}

// ── Helpers for VisualRuleBuilder integration ───────────────

/** Lift a Match into a guaranteed AllOf/AnyOf shape so the
 *  editor has a root group to render. If the Match is already a
 *  combinator, return as-is. If it's a single Condition, wrap
 *  in an AllOf with that one child. */
export function liftToGroup(m: Match): AllOf | AnyOf {
  if (isAllOf(m) || isAnyOf(m)) return m;
  return { all: [m] };
}

/** Inverse of liftToGroup: if the editor produces a group with
 *  exactly one Condition child, the canonical form is the raw
 *  Condition (the rule DSL prefers it). If it has multiple
 *  children OR any group child, keep the combinator shape. */
export function unliftFromGroup(g: AllOf | AnyOf): Match {
  const children = childrenOf(g);
  if (children.length === 1 && isCondition(children[0]!)) {
    return children[0]!;
  }
  return g;
}

/** Compute the maximum nesting depth of a Match tree. Used by
 *  callers that want to surface "you've reached the depth cap"
 *  warnings before the operator clicks. */
export function depthOf(m: Match): number {
  if (isCondition(m)) return 0;
  const children = isAllOf(m) ? m.all : (m as AnyOf).any;
  return 1 + Math.max(0, ...children.map(depthOf));
}
