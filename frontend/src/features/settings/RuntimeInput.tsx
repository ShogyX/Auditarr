/**
 * Stage 2 — Runtime settings per-type input.
 *
 * Extracted from the inline ``RuntimeInput`` in RuntimeSettingsPanel.
 * Four branches based on the field's type and constraint shape:
 *
 *   - ``boolean`` → role="switch" toggle with checked state
 *   - enum (pattern-derived) → <select> with the parsed options
 *   - ``integer`` / ``number`` → <input type="number"> with ge/le
 *     constraints, with the standard "fall back to env default on
 *     empty" recovery
 *   - everything else → free-text <input>
 *
 * The pre-Stage-2 panel used local ``.settings-input`` / ``.settings-
 * switch`` CSS classes for these. We keep that vocabulary because
 * the existing test asserts ``getByRole("combobox")`` (the <select>
 * branch for log_level) and ``getByRole("button", { name: /restore
 * default/i })`` — both unaffected by class names — so adopting the
 * Stage 1 ``Input``/``Select``/``Switch`` primitives is safe here.
 * Adopting them retires three more hand-rolled control sites.
 */

import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import type { RuntimeField } from "@/hooks/useRuntimeSettings";

import type { EditValue } from "./runtimeSettingsShared";

export interface RuntimeInputProps {
  field: RuntimeField;
  value: EditValue;
  onChange: (v: EditValue) => void;
}

export function RuntimeInput({ field, value, onChange }: RuntimeInputProps) {
  if (field.type === "boolean") {
    const v = !!value;
    return (
      <Switch
        checked={v}
        onCheckedChange={(next) => onChange(next)}
        aria-label={field.label}
      />
    );
  }
  if (field.options) {
    return (
      <Select
        style={{ maxWidth: 260 }}
        value={String(value)}
        onChange={(e) => onChange(e.target.value)}
      >
        {field.options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </Select>
    );
  }
  if (field.type === "integer" || field.type === "number") {
    return (
      <Input
        type="number"
        variant="mono"
        style={{ width: 140 }}
        value={typeof value === "number" ? value : Number(value) || 0}
        min={field.constraints.ge}
        max={field.constraints.le}
        onChange={(e) => {
          // Empty string in a number input shouldn't blow up the
          // parser — fall back to the field's env default so the
          // edit stays well-typed.
          const raw = e.target.value;
          if (raw === "") {
            onChange(field.env_default as EditValue);
            return;
          }
          const n = Number(raw);
          if (Number.isFinite(n)) onChange(n);
        }}
      />
    );
  }
  return (
    <Input
      type="text"
      variant="mono"
      style={{ width: "min(420px, 100%)" }}
      value={String(value)}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}
