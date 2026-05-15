/**
 * Stage 15: Visual rule builder.
 *
 * Renders a rule's match-tree + actions as three columns:
 *   Trigger          Conditions          Actions
 *                    WHEN/AND/OR chips   set_severity / add_tag / ...
 *
 * The component is stateless — it reads a ``RuleDefinition`` from the
 * dialog and emits patches back via ``onChange``. The dialog owns the
 * authoritative state so flipping between Form/Visual/JSON keeps the
 * same definition.
 *
 * The condition tree allowed by the backend is:
 *   - a leaf condition  {field, op, value}
 *   - an ``all`` combinator  {all: [Match, Match, ...]}
 *   - an ``any`` combinator  {any: [Match, Match, ...]}
 *
 * To keep the visual builder tractable we collapse the tree into a
 * single flat list with a top-level combinator (``all`` or ``any``).
 * Nested combinators get serialized back faithfully from a JSON edit
 * but won't round-trip through the visual editor — they're flagged
 * with a banner that explains the user needs JSON mode for nested
 * logic. This matches the proposed mockup which only renders
 * single-level WHEN/AND/OR.
 */

import { useMemo, type ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";
import type {
  Action,
  AllOf,
  AnyOf,
  Condition,
  Match,
  RuleDefinition,
  RuleVocabulary,
  RuleVocabularyField,
} from "@/hooks/useRules";

// ── Type guards ──────────────────────────────────────────────
function isCondition(m: Match): m is Condition {
  return (m as Condition).field !== undefined;
}
function isAllOf(m: Match): m is AllOf {
  return Array.isArray((m as AllOf).all);
}
function isAnyOf(m: Match): m is AnyOf {
  return Array.isArray((m as AnyOf).any);
}

// ── Operator labels (Stage 4 audit fix, Issue 6) ─────────────
// The backend's match operators come back as short opaque tokens
// (``eq``, ``gt``, ``not_contains``, etc.). The previous renderer
// did ``op.replace(/_/g, " ")``, which only smooths underscores —
// "eq" stayed as "eq" and so on. No operator who isn't already
// steeped in the schema can read those at a glance.
//
// The map below covers every operator the rule vocabulary
// currently emits. Anything we forgot here still renders via the
// fallback at the call site (``OP_LABELS[op] ?? op.replace(...)``),
// so adding a new backend operator never produces a blank option.
const OP_LABELS: Record<string, string> = {
  eq: "equals",
  neq: "does not equal",
  gt: "greater than",
  gte: "greater than or equal",
  lt: "less than",
  lte: "less than or equal",
  contains: "contains",
  not_contains: "does not contain",
  regex: "matches regex",
  in: "is one of",
  not_in: "is not one of",
  any_of: "contains any of",
  none_of: "contains none of",
};

interface Flattened {
  combinator: "all" | "any";
  conditions: Condition[];
  /** True if the original match tree had nested combinators we
      flattened away. The builder shows a warning and the JSON view
      remains the source of truth. */
  hadNesting: boolean;
}

/** Best-effort flatten of a Match tree into [combinator, conditions[]]. */
function flatten(match: Match): Flattened {
  if (isCondition(match)) {
    return { combinator: "all", conditions: [match], hadNesting: false };
  }
  if (isAllOf(match)) {
    const leaves: Condition[] = [];
    let nested = false;
    for (const child of match.all) {
      if (isCondition(child)) leaves.push(child);
      else {
        nested = true;
        // Best-effort: pull conditions from the nested combinator too,
        // so the user at least sees them rather than getting an empty
        // builder. The hadNesting flag warns about lossy round-trip.
        const f = flatten(child);
        leaves.push(...f.conditions);
      }
    }
    return { combinator: "all", conditions: leaves, hadNesting: nested };
  }
  if (isAnyOf(match)) {
    const leaves: Condition[] = [];
    let nested = false;
    for (const child of match.any) {
      if (isCondition(child)) leaves.push(child);
      else {
        nested = true;
        const f = flatten(child);
        leaves.push(...f.conditions);
      }
    }
    return { combinator: "any", conditions: leaves, hadNesting: nested };
  }
  return { combinator: "all", conditions: [], hadNesting: false };
}

/** Re-shape a flattened (combinator + conditions[]) back into a Match. */
function rebuild(combinator: "all" | "any", conditions: Condition[]): Match {
  if (conditions.length === 1) return conditions[0]!;
  return combinator === "all" ? { all: conditions } : { any: conditions };
}

// ── Main component ──────────────────────────────────────────
export function VisualRuleBuilder({
  definition,
  vocabulary,
  onChange,
}: {
  definition: RuleDefinition;
  vocabulary: RuleVocabulary;
  onChange: (next: RuleDefinition) => void;
}) {
  const flattened = useMemo(() => flatten(definition.match), [definition.match]);

  // Builder operations — every mutation produces a new RuleDefinition
  // that we push up to the dialog.
  function setCombinator(combinator: "all" | "any") {
    onChange({
      ...definition,
      match: rebuild(combinator, flattened.conditions),
    });
  }

  function setCondition(idx: number, patch: Partial<Condition>) {
    const next = flattened.conditions.map((c, i) => (i === idx ? { ...c, ...patch } : c));
    onChange({ ...definition, match: rebuild(flattened.combinator, next) });
  }

  function addCondition() {
    const firstField = vocabulary.fields[0];
    if (!firstField) return;
    const firstOp = vocabulary.ops[firstField.type]?.[0] ?? "eq";
    const newCond: Condition = {
      field: firstField.key,
      op: firstOp,
      value: defaultValueFor(firstField),
    };
    onChange({
      ...definition,
      match: rebuild(flattened.combinator, [...flattened.conditions, newCond]),
    });
  }

  function removeCondition(idx: number) {
    const next = flattened.conditions.filter((_, i) => i !== idx);
    if (next.length === 0) return; // Always keep at least one row.
    onChange({ ...definition, match: rebuild(flattened.combinator, next) });
  }

  function setAction(idx: number, action: Action) {
    onChange({
      ...definition,
      actions: definition.actions.map((a, i) => (i === idx ? action : a)),
    });
  }

  function addAction() {
    const first = vocabulary.actions[0];
    if (!first) return;
    const fresh = freshAction(first.type);
    onChange({ ...definition, actions: [...definition.actions, fresh] });
  }

  function removeAction(idx: number) {
    const next = definition.actions.filter((_, i) => i !== idx);
    if (next.length === 0) return;
    onChange({ ...definition, actions: next });
  }

  return (
    <div className="flex flex-col gap-3">
      {flattened.hadNesting ? (
        <div className="text-[11.5px] p-2 rounded-md bg-sev-warn/10 text-sev-warn border border-sev-warn/30">
          <Icon name="alert" size={11} className="inline mr-1" />
          This rule has nested combinators that don't fit the flat visual layout. The conditions
          below are shown for reference; edit JSON mode for full control.
        </div>
      ) : null}

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_2fr_1fr] gap-3">
        {/* ── Trigger ── */}
        <Column title="Trigger">
          <div className="p-3 border border-border rounded-md bg-surface-2">
            <div className="text-[11px] uppercase tracking-[0.06em] text-muted-2 mb-1 font-semibold">
              When
            </div>
            <div className="text-[12.5px] font-mono">file scanned or re-evaluated</div>
            <div className="text-[10.5px] text-muted-2 mt-1">
              All rules evaluate on every scan and after rule edits.
            </div>
          </div>
        </Column>

        {/* ── Conditions ── */}
        <Column
          title={`If ${flattened.combinator === "all" ? "all" : "any"} of:`}
          right={
            <div className="inline-flex border border-border rounded-md bg-surface-2 p-0.5">
              {(["all", "any"] as const).map((k) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => setCombinator(k)}
                  className={cn(
                    "text-[11.5px] px-2 py-0.5 rounded",
                    flattened.combinator === k
                      ? "bg-surface text-text"
                      : "text-text-2 hover:bg-[var(--hover)]",
                  )}
                >
                  {k}
                </button>
              ))}
            </div>
          }
        >
          <div className="flex flex-col gap-2">
            {flattened.conditions.map((cond, idx) => (
              <ConditionRow
                key={idx}
                cond={cond}
                idx={idx}
                combinator={flattened.combinator}
                vocabulary={vocabulary}
                onChange={(patch) => setCondition(idx, patch)}
                onRemove={flattened.conditions.length > 1 ? () => removeCondition(idx) : undefined}
              />
            ))}
            <Button size="sm" variant="ghost" onClick={addCondition}>
              <Icon name="plus" size={12} />
              <span className="ml-1">Add condition</span>
            </Button>
          </div>
        </Column>

        {/* ── Actions ── */}
        <Column title="Then do:">
          <div className="flex flex-col gap-2">
            {definition.actions.map((action, idx) => (
              <ActionRow
                key={idx}
                action={action}
                vocabulary={vocabulary}
                onChange={(next) => setAction(idx, next)}
                onRemove={definition.actions.length > 1 ? () => removeAction(idx) : undefined}
              />
            ))}
            <Button size="sm" variant="ghost" onClick={addAction}>
              <Icon name="plus" size={12} />
              <span className="ml-1">Add action</span>
            </Button>
          </div>
        </Column>
      </div>
    </div>
  );
}

// ── Column wrapper ──────────────────────────────────────────
function Column({
  title,
  right,
  children,
}: {
  title: string;
  right?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2 min-w-0">
      <div className="flex items-center justify-between">
        <div className="text-[10.5px] uppercase tracking-[0.06em] font-semibold text-muted-2">
          {title}
        </div>
        {right}
      </div>
      {children}
    </div>
  );
}

// ── Condition row ───────────────────────────────────────────
function ConditionRow({
  cond,
  idx,
  combinator,
  vocabulary,
  onChange,
  onRemove,
}: {
  cond: Condition;
  idx: number;
  combinator: "all" | "any";
  vocabulary: RuleVocabulary;
  onChange: (patch: Partial<Condition>) => void;
  onRemove?: () => void;
}) {
  const fieldDef = vocabulary.fields.find((f) => f.key === cond.field);
  const fieldType = fieldDef?.type ?? "string";
  const validOps = vocabulary.ops[fieldType] ?? [];
  const conjunction = idx === 0 ? "WHEN" : combinator === "all" ? "AND" : "OR";

  return (
    <div className="flex items-center gap-2 flex-wrap p-2 border border-border rounded-md bg-surface">
      <span
        className={cn(
          "font-mono text-[10.5px] font-semibold tracking-[0.04em] w-10 text-right",
          idx === 0 ? "text-text-2" : "text-muted-2",
        )}
      >
        {conjunction}
      </span>
      <select
        aria-label="Field"
        value={cond.field}
        onChange={(e) => {
          const next = vocabulary.fields.find((f) => f.key === e.target.value);
          if (!next) return;
          const nextOps = vocabulary.ops[next.type] ?? [];
          // If the current op isn't valid for the new field's type,
          // fall back to the first op for that type.
          const op = nextOps.includes(cond.op) ? cond.op : (nextOps[0] ?? "eq");
          onChange({ field: e.target.value, op, value: defaultValueFor(next) });
        }}
        className="h-7 px-2 text-[12px] bg-surface-2 border border-border rounded focus:outline-none focus:ring-2 focus:ring-accent"
      >
        {vocabulary.fields.map((f) => (
          <option key={f.key} value={f.key}>
            {f.label}
          </option>
        ))}
      </select>
      <select
        aria-label="Operator"
        value={cond.op}
        onChange={(e) => onChange({ op: e.target.value })}
        className="h-7 px-2 text-[12px] bg-surface-2 border border-border rounded focus:outline-none focus:ring-2 focus:ring-accent"
      >
        {validOps.map((op) => (
          <option key={op} value={op}>
            {OP_LABELS[op] ?? op.replace(/_/g, " ")}
          </option>
        ))}
      </select>
      <ValueInput fieldDef={fieldDef} value={cond.value} onChange={(v) => onChange({ value: v })} />
      {onRemove ? (
        <Button size="sm" variant="ghost" onClick={onRemove} title="Remove condition">
          <Icon name="trash" size={12} />
        </Button>
      ) : null}
    </div>
  );
}

// ── Value input — typed by field ────────────────────────────
function ValueInput({
  fieldDef,
  value,
  onChange,
}: {
  fieldDef: RuleVocabularyField | undefined;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const className =
    "h-7 px-2 text-[12px] bg-surface-2 border border-border rounded focus:outline-none focus:ring-2 focus:ring-accent flex-1 min-w-[120px]";

  if (!fieldDef) {
    return (
      <input
        className={className}
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }

  if (fieldDef.enum) {
    return (
      <select
        className={className}
        value={String(value ?? fieldDef.enum[0] ?? "")}
        onChange={(e) => onChange(e.target.value)}
      >
        {fieldDef.enum.map((opt) => (
          <option key={opt} value={opt}>
            {opt}
          </option>
        ))}
      </select>
    );
  }

  if (fieldDef.type === "bool") {
    return (
      <select
        className={className}
        value={value === true ? "true" : "false"}
        onChange={(e) => onChange(e.target.value === "true")}
      >
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    );
  }

  if (fieldDef.type === "numeric") {
    return (
      <input
        type="number"
        className={className}
        value={typeof value === "number" ? value : ""}
        onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))}
      />
    );
  }

  if (fieldDef.type === "array") {
    // Array fields accept either a single value (for contains/not_contains)
    // or a list (for any_of/none_of). We render a comma-separated input
    // and parse on commit — pragmatic over perfect.
    const display = Array.isArray(value) ? value.join(", ") : String(value ?? "");
    return (
      <input
        className={className}
        value={display}
        placeholder="e.g. eng, jpn, fre"
        onChange={(e) => {
          const raw = e.target.value;
          const parts = raw
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean);
          onChange(parts.length <= 1 ? (parts[0] ?? "") : parts);
        }}
      />
    );
  }

  // Plain string
  return (
    <input
      className={className}
      value={String(value ?? "")}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

// ── Action row ──────────────────────────────────────────────
function ActionRow({
  action,
  vocabulary,
  onChange,
  onRemove,
}: {
  action: Action;
  vocabulary: RuleVocabulary;
  onChange: (next: Action) => void;
  onRemove?: () => void;
}) {
  const def = vocabulary.actions.find((a) => a.type === action.type);
  return (
    <div className="flex items-center gap-2 flex-wrap p-2 border border-border rounded-md bg-surface">
      <select
        aria-label="Action type"
        value={action.type}
        onChange={(e) => onChange(freshAction(e.target.value))}
        className="h-7 px-2 text-[12px] bg-surface-2 border border-border rounded focus:outline-none focus:ring-2 focus:ring-accent"
      >
        {vocabulary.actions.map((a) => (
          <option key={a.type} value={a.type}>
            {a.label}
          </option>
        ))}
      </select>
      {def
        ? Object.entries(def.args_schema).map(([argKey, argDef]) => (
            <ActionArgInput
              key={argKey}
              argKey={argKey}
              argDef={argDef}
              value={(action as Record<string, unknown>)[argKey]}
              onChange={(v) => onChange({ ...action, [argKey]: v } as Action)}
            />
          ))
        : null}
      {onRemove ? (
        <Button size="sm" variant="ghost" onClick={onRemove} title="Remove action">
          <Icon name="trash" size={12} />
        </Button>
      ) : null}
    </div>
  );
}

function ActionArgInput({
  argKey,
  argDef,
  value,
  onChange,
}: {
  argKey: string;
  argDef: { type: string; enum?: string[]; required?: boolean; hint?: string };
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const className =
    "h-7 px-2 text-[12px] bg-surface-2 border border-border rounded focus:outline-none focus:ring-2 focus:ring-accent flex-1 min-w-[120px]";

  if (argDef.enum) {
    return (
      <label className="flex items-center gap-1.5 text-[11.5px] text-muted-2">
        {argKey}
        <select
          className={className}
          value={String(value ?? argDef.enum[0] ?? "")}
          onChange={(e) => onChange(e.target.value)}
        >
          {argDef.enum.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      </label>
    );
  }
  // Stage 9 (audit follow-up): boolean arg renderer. ``Delete``'s
  // ``confirm`` is the only boolean today. Surface it as a labeled
  // checkbox so the hard-delete semantics is visible and
  // deliberately checkable, not buried as a typed-into "true" string.
  if (argDef.type === "boolean") {
    return (
      <label
        className="flex items-center gap-1.5 text-[11.5px] text-muted-2"
        title={argDef.hint}
      >
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
        {argKey}
        {argDef.hint ? (
          <span className="text-muted">({argDef.hint})</span>
        ) : null}
      </label>
    );
  }
  return (
    <label className="flex items-center gap-1.5 text-[11.5px] text-muted-2">
      {argKey}
      <input
        className={className}
        value={String(value ?? "")}
        placeholder={argDef.hint}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

// ── Defaults ────────────────────────────────────────────────
function defaultValueFor(field: RuleVocabularyField): unknown {
  if (field.enum) return field.enum[0] ?? "";
  if (field.type === "numeric") return 0;
  if (field.type === "bool") return true;
  if (field.type === "array") return "";
  return "";
}

function freshAction(type: string): Action {
  switch (type) {
    case "set_severity":
      return { type: "set_severity", severity: "warn" };
    case "add_tag":
      return { type: "add_tag", tag: "" };
    case "queue_optimization":
      return { type: "queue_optimization", profile: "" };
    case "notify":
      return { type: "notify", channel: "", message: null };
    // Stage 9 (audit follow-up): defaults match the schema defaults.
    // ``quarantine`` has no required fields. ``delete`` defaults
    // ``confirm`` to false so the picker's first-clicked state is
    // the SAFE soft-delete; the operator must explicitly flip
    // confirm to true for a hard delete.
    case "quarantine":
      return { type: "quarantine", reason: null };
    case "delete":
      return { type: "delete", confirm: false };
    default:
      return { type: "set_severity", severity: "warn" };
  }
}
