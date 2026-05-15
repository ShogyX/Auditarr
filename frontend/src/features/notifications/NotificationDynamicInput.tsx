/**
 * Stage 6 — Schema-driven form input for notification channels.
 *
 * Variant of the Integrations DynamicInput. Adds support for ``enum``
 * schema properties (rendering a select) — notification config
 * schemas use enums more often (channel types, severity formats)
 * than integration schemas do.
 *
 * Stage 15 (audit follow-up): adds an ``object`` variant for the
 * webhook provider's ``headers`` field. Renders a small key/value
 * editor so operators don't have to hand-type JSON.
 *
 * Uses the Stage 1 ``Input`` and ``Select`` primitives.
 */

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";

export interface NotificationDynamicInputProps {
  meta: { type?: string; enum?: unknown[] };
  value: unknown;
  onChange: (v: unknown) => void;
}

export function NotificationDynamicInput({
  meta,
  value,
  onChange,
}: NotificationDynamicInputProps) {
  if (meta.enum && meta.enum.length > 0) {
    return (
      <Select
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
      >
        {meta.enum.map((opt) => (
          <option key={String(opt)} value={String(opt)}>
            {String(opt)}
          </option>
        ))}
      </Select>
    );
  }
  if (meta.type === "boolean") {
    return (
      <input
        type="checkbox"
        checked={Boolean(value)}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4"
      />
    );
  }
  if (meta.type === "integer") {
    return (
      <Input
        type="number"
        value={String(value ?? "")}
        onChange={(e) => onChange(parseInt(e.target.value, 10) || 0)}
      />
    );
  }
  // Stage 15 (audit follow-up): key/value editor for object fields.
  // The webhook provider's ``headers`` uses this. Hand-typed JSON
  // works but a structured editor avoids quoting mistakes.
  if (meta.type === "object") {
    return <ObjectKVInput value={value} onChange={onChange} />;
  }
  return (
    <Input
      value={String(value ?? "")}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

/** Small key/value editor backing the ``object`` field type.
 *
 *  The value is normalized to a flat ``Record<string, string>``; the
 *  parent only sees pairs with a non-empty key (the commit() helper
 *  strips empties). Pending rows — added but not yet typed — live in
 *  local state so the UI can render the input row immediately after
 *  the operator clicks "Add header" without flickering on parent
 *  re-renders.
 */
function ObjectKVInput({
  value,
  onChange,
}: {
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const fromParent = useMemo<[string, string][]>(() => {
    if (value && typeof value === "object" && !Array.isArray(value)) {
      return Object.entries(value as Record<string, unknown>).map(
        ([k, v]) => [k, String(v ?? "")],
      );
    }
    return [];
  }, [value]);

  // Number of pending (empty-key) rows the user has added but not
  // yet keyed in. Live locally so add-row doesn't depend on the
  // parent committing the empty row back.
  const [pendingRows, setPendingRows] = useState<[string, string][]>([]);

  const allRows: [string, string][] = [...fromParent, ...pendingRows];

  const commit = (next: [string, string][]) => {
    const out: Record<string, string> = {};
    const pending: [string, string][] = [];
    for (const [k, v] of next) {
      if (k.trim()) out[k] = v;
      else pending.push([k, v]);
    }
    setPendingRows(pending);
    onChange(out);
  };

  return (
    <div className="flex flex-col gap-1.5" data-testid="object-kv-input">
      {allRows.map(([k, v], idx) => (
        <div key={idx} className="flex items-center gap-1.5">
          <Input
            value={k}
            placeholder="Header name"
            aria-label="Header name"
            onChange={(e) => {
              const next = [...allRows];
              next[idx] = [e.target.value, v];
              commit(next);
            }}
          />
          <Input
            value={v}
            placeholder="Value"
            aria-label="Header value"
            onChange={(e) => {
              const next = [...allRows];
              next[idx] = [k, e.target.value];
              commit(next);
            }}
          />
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => {
              const next = allRows.filter((_, i) => i !== idx);
              commit(next);
            }}
            aria-label={`Remove header ${k || idx}`}
            title="Remove"
          >
            <Icon name="trash" size={12} />
          </Button>
        </div>
      ))}
      <Button
        type="button"
        size="sm"
        variant="ghost"
        onClick={() => setPendingRows([...pendingRows, ["", ""]])}
        title="Add header"
      >
        <Icon name="plus" size={12} />
        <span className="ml-1">Add header</span>
      </Button>
    </div>
  );
}
