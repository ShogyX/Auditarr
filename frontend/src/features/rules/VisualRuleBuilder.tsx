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
import { useMediaVocabulary, type MediaVocabulary } from "@/hooks/useMedia";
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
    const nextActions = definition.actions.map((a, i) => (i === idx ? action : a));
    onChange(syncAck({ ...definition, actions: nextActions }, vocabulary));
  }

  function addAction() {
    const first = vocabulary.actions[0];
    if (!first) return;
    const fresh = freshAction(first.type);
    onChange(
      syncAck(
        { ...definition, actions: [...definition.actions, fresh] },
        vocabulary,
      ),
    );
  }

  function removeAction(idx: number) {
    const next = definition.actions.filter((_, i) => i !== idx);
    if (next.length === 0) return;
    onChange(syncAck({ ...definition, actions: next }, vocabulary));
  }

  // Stage 06 (v1.7) — destructive-action acknowledgement (addendum
  // A.0.1). The backend rejects rule bodies with a delete action
  // unless ``acknowledged_destructive: true`` is set. The flag
  // metadata comes from ``vocabulary.rule_flags`` so the label /
  // hint text stays server-authoritative.
  const ackFlag = vocabulary.rule_flags?.acknowledged_destructive;
  const hasDelete = definition.actions.some((a) => a.type === "delete");
  const ackChecked = definition.acknowledged_destructive === true;

  function setAck(checked: boolean) {
    onChange({
      ...definition,
      acknowledged_destructive: checked ? true : undefined,
    });
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

      {/* Stage 06 (v1.7) — destructive-action acknowledgement.
          Visible only when the rule contains at least one delete
          action; the backend rejects on save without this flag.
          The flag's label + hint come from
          ``vocabulary.rule_flags`` so the server stays
          authoritative for the wording (per addendum A.0.1).
      */}
      {ackFlag && hasDelete ? (
        <div
          className={cn(
            "p-2.5 rounded-md border text-[12px]",
            ackChecked
              ? "bg-sev-warn/5 border-sev-warn/30 text-text"
              : "bg-sev-error/10 border-sev-error/40 text-text",
          )}
          data-testid="acknowledged-destructive-section"
        >
          <label className="flex items-start gap-2 cursor-pointer">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={ackChecked}
              onChange={(e) => setAck(e.target.checked)}
              aria-label={ackFlag.label}
            />
            <div className="flex flex-col">
              <span className="font-medium">{ackFlag.label}</span>
              {ackFlag.hint ? (
                <span className="text-[11px] text-muted-2 mt-0.5">{ackFlag.hint}</span>
              ) : null}
              {!ackChecked ? (
                <span className="text-[11px] text-sev-error mt-1">
                  <Icon name="alert" size={10} className="inline mr-1" />
                  This rule will not save until acknowledged.
                </span>
              ) : null}
            </div>
          </label>
        </div>
      ) : null}
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

  // Stage 15 (plan §657) — for codec / container / extension /
  // tag fields, fetch the library's actual vocabulary and offer
  // it as a datalist alongside the free-text input. We keep
  // free-text so operators can author rules for values that
  // haven't been indexed yet (e.g. a new codec that'll appear
  // after the next scan).
  const vocab = useMediaVocabulary();
  const vocabSlice = fieldDef
    ? vocabularySliceFor(fieldDef.key, vocab.data)
    : null;

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

  // Stage 15 — for string fields that match the library
  // vocabulary, augment with a datalist. Browsers fall back
  // gracefully to plain input when datalist isn't supported.
  if (vocabSlice && vocabSlice.length > 0) {
    const datalistId = `vocab-${fieldDef.key}`;
    return (
      <>
        <input
          className={className}
          value={String(value ?? "")}
          onChange={(e) => onChange(e.target.value)}
          list={datalistId}
          data-testid={`rule-value-input-${fieldDef.key}`}
        />
        <datalist
          id={datalistId}
          data-testid={`rule-value-datalist-${fieldDef.key}`}
        >
          {vocabSlice.map((opt) => (
            <option key={opt} value={opt} />
          ))}
        </datalist>
      </>
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

/**
 * Stage 15 helper — map a rule field key to the matching slice
 * of the media vocabulary. Returns null when the field doesn't
 * have a library-driven value list (e.g. numeric / bool fields,
 * or string fields like ``filename`` whose values are unbounded).
 */
function vocabularySliceFor(
  key: string,
  vocab: MediaVocabulary | undefined,
): string[] | null {
  if (!vocab) return null;
  switch (key) {
    case "video_codec":
      return vocab.video_codecs;
    case "audio_codec":
      return vocab.audio_codecs;
    case "container":
      return vocab.containers;
    case "extension":
      return vocab.extensions;
    case "tags":
      // The ``tags`` field is an array-typed rule field
      // (handled above) — this slice is a fallback for any
      // string-typed tag variant a future stage might add.
      return vocab.tags;
    default:
      return null;
  }
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
  argDef: {
    type: string;
    enum?: string[];
    required?: boolean;
    hint?: string;
    /** Stage 06 (v1.7): nested-object args carry ``properties``
     *  (used by Notify's ``throttle`` block). Numeric children
     *  may also carry ``minimum`` for client-side validation. */
    properties?: Record<
      string,
      {
        type: string;
        minimum?: number;
        required?: boolean;
        hint?: string;
      }
    >;
    minimum?: number;
  };
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const className =
    "h-7 px-2 text-[12px] bg-surface-2 border border-border rounded focus:outline-none focus:ring-2 focus:ring-accent flex-1 min-w-[120px]";

  // Stage 06 (v1.7) — object-typed args. Today only ``throttle``
  // on Notify lands here; a generic renderer keeps future
  // nested args working with no code change. Object args are
  // optional + collapse to ``null`` when empty.
  if (argDef.type === "object" && argDef.properties) {
    return (
      <ObjectArgInput
        argKey={argKey}
        argDef={
          argDef as {
            type: string;
            hint?: string;
            properties: Record<
              string,
              { type: string; minimum?: number; required?: boolean; hint?: string }
            >;
          }
        }
        value={value as Record<string, unknown> | null | undefined}
        onChange={onChange}
      />
    );
  }

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
  // Stage 9 (audit follow-up): boolean arg renderer. Pre-Stage-05
  // this rendered ``Delete.confirm``; Stage 05 retired that flag,
  // and no action in the current vocabulary publishes a boolean
  // arg. The renderer stays for forward compatibility — a future
  // action shipping a boolean arg gets a labeled checkbox without
  // any extra wiring.
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

// Stage 06 (v1.7) — nested-object arg renderer. The Notify
// action's ``throttle`` is the first object-typed arg; this
// component renders a collapsed toggle that expands into one
// numeric input per declared property. When the toggle is off,
// the value is ``null`` (the backend reads "throttle unset");
// when on, the value is ``{prop: number, prop: number, ...}``.
//
// Numeric children use ``argDef.properties[k].minimum`` for
// client-side validation — the backend's schema validator
// catches sub-minimum values too, but a hint here helps the
// operator avoid round-trip rejections.
function ObjectArgInput({
  argKey,
  argDef,
  value,
  onChange,
}: {
  argKey: string;
  argDef: {
    type: string;
    hint?: string;
    properties: Record<
      string,
      { type: string; minimum?: number; required?: boolean; hint?: string }
    >;
  };
  value: Record<string, unknown> | null | undefined;
  onChange: (v: unknown) => void;
}) {
  const enabled = value != null;

  function toggle(on: boolean) {
    if (on) {
      // Seed every property with its minimum (or 0) so the body
      // is always shape-valid against the backend's schema.
      const seed: Record<string, unknown> = {};
      for (const [k, p] of Object.entries(argDef.properties)) {
        seed[k] = p.minimum ?? 0;
      }
      onChange(seed);
    } else {
      onChange(null);
    }
  }

  function setChild(childKey: string, raw: string) {
    const next = { ...(value ?? {}) };
    const n = Number(raw);
    next[childKey] = Number.isFinite(n) ? n : 0;
    onChange(next);
  }

  const className =
    "h-7 px-2 text-[12px] bg-surface-2 border border-border rounded focus:outline-none focus:ring-2 focus:ring-accent w-[110px]";

  return (
    <div className="flex flex-col gap-1 w-full border-t border-border mt-1 pt-2">
      <label
        className="flex items-center gap-1.5 text-[11.5px] text-muted-2"
        title={argDef.hint}
      >
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => toggle(e.target.checked)}
        />
        <span className="font-medium">{argKey}</span>
        {argDef.hint ? (
          <span className="text-muted text-[11px]">({argDef.hint})</span>
        ) : null}
      </label>
      {enabled ? (
        <div className="flex items-center gap-3 flex-wrap pl-5">
          {Object.entries(argDef.properties).map(([k, p]) => (
            <label
              key={k}
              className="flex items-center gap-1 text-[11.5px] text-muted-2"
              title={p.hint}
            >
              {k}
              <input
                type="number"
                className={className}
                min={p.minimum}
                value={String(value?.[k] ?? p.minimum ?? 0)}
                onChange={(e) => setChild(k, e.target.value)}
              />
            </label>
          ))}
        </div>
      ) : null}
    </div>
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

// Stage 06 (v1.7) — destructive-action acknowledgement helper.
// The backend rejects ``acknowledged_destructive: true`` on rules
// without a delete action AND rejects its absence on rules with
// one. The visual builder mediates: whenever the action list
// changes such that no delete remains, strip the flag so the
// next save doesn't trip the "forbidden" branch. When a delete
// is present, leave the flag untouched — the operator's checkbox
// click is what writes True there.
function syncAck(
  definition: RuleDefinition,
  _vocabulary: RuleVocabulary,
): RuleDefinition {
  const hasDelete = definition.actions.some((a) => a.type === "delete");
  if (!hasDelete && definition.acknowledged_destructive) {
    const { acknowledged_destructive: _drop, ...rest } = definition;
    void _drop;
    return rest;
  }
  return definition;
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
    // Stage 9 (audit follow-up), updated Stage 05 (v1.7): ``delete``
    // is now unconditional with an optional ``reason`` recorded in
    // the audit log. Stage 05 retired the Stage 9 ``quarantine``
    // action and the Stage 9 ``confirm`` flag on Delete.
    case "delete":
      return { type: "delete", reason: null };
    default:
      return { type: "set_severity", severity: "warn" };
  }
}
