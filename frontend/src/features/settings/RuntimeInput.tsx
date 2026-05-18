/**
 * Stage 2 — Runtime settings per-type input.
 *
 * Stage 02 (v1.7) adds: for the ``scanner_max_file_size_mb``
 * runtime field, render a slider + numeric input pair instead of
 * the bare number input. The slider uses a log-ish step ladder
 * (1, 5, 10, 25, 50, 100, 250, 500, 1024, 2048, 5120, 10240,
 * 20480, 51200, 102400) so the operator can pick reasonable
 * sizes from 1 MB to 100 GB with one drag. The number field
 * stays in sync so an operator who wants an exact value can type
 * it. The displayed unit auto-switches: < 1024 → MB, ≥ 1024 → GB.
 *
 * Extracted from the inline ``RuntimeInput`` in RuntimeSettingsPanel.
 * Four branches based on the field's type and constraint shape:
 *
 *   - ``boolean`` → role="switch" toggle with checked state
 *   - enum (pattern-derived) → <select> with the parsed options
 *   - ``integer`` / ``number`` → <input type="number"> with ge/le
 *     constraints, with the standard "fall back to env default on
 *     empty" recovery (or the new slider for the size-mb key)
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

/**
 * Stage 02 — discrete step ladder for the file-size slider.
 *
 * Linear would cover 1–102400 MB with one MB granularity that's
 * useless to the operator at any large value. The ladder gives
 * useful resolution at every order of magnitude.
 */
export const SCANNER_MAX_FILE_SIZE_LADDER: readonly number[] = [
  1, 5, 10, 25, 50, 100, 250, 500, 1024, 2048, 5120, 10240, 20480, 51200,
  102400,
] as const;

/** Map an arbitrary MB value to the closest ladder index. */
function ladderIndexForValue(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 0;
  let best = 0;
  let bestDiff = Infinity;
  for (let i = 0; i < SCANNER_MAX_FILE_SIZE_LADDER.length; i += 1) {
    // TS strict mode flags array indexing as possibly-undefined
    // even when bounded by length; the loop guards i so the cast
    // is safe.
    const step = SCANNER_MAX_FILE_SIZE_LADDER[i] as number;
    const diff = Math.abs(step - value);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = i;
    }
  }
  return best;
}

/** Render a MB value with KB / MB / GB unit suffix as appropriate. */
export function formatFileSizeMB(value: number): { value: string; unit: string } {
  if (!Number.isFinite(value)) return { value: "—", unit: "MB" };
  if (value < 1) {
    return { value: Math.round(value * 1024).toString(), unit: "KB" };
  }
  if (value < 1024) {
    return { value: value.toString(), unit: "MB" };
  }
  // value >= 1024 ⇒ display as GB with up to 1 decimal place
  const gb = value / 1024;
  return {
    value: Number.isInteger(gb) ? gb.toString() : gb.toFixed(1),
    unit: "GB",
  };
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
  // Stage 02 — slider variant for the file-size knob. Detected
  // by the field's persisted key so other size-style fields can
  // opt in later by name.
  if (
    (field.type === "integer" || field.type === "number") &&
    field.key === "scanner_max_file_size_mb"
  ) {
    const numeric =
      typeof value === "number" ? value : Number(value) || 0;
    const idx = ladderIndexForValue(numeric);
    const display = formatFileSizeMB(numeric);
    return (
      <div
        className="runtime-slider"
        role="group"
        aria-label="Scanner maximum file size"
      >
        <div className="runtime-slider-row">
          <input
            type="range"
            className="runtime-slider-track"
            min={0}
            max={SCANNER_MAX_FILE_SIZE_LADDER.length - 1}
            step={1}
            value={idx}
            aria-label={field.label}
            onChange={(e) => {
              const i = Number(e.target.value);
              const next = SCANNER_MAX_FILE_SIZE_LADDER[i] ?? numeric;
              onChange(next);
            }}
          />
          <span className="runtime-slider-value">
            {display.value}
            <span className="runtime-slider-unit">{display.unit}</span>
          </span>
        </div>
        <Input
          type="number"
          variant="mono"
          style={{ width: 140 }}
          value={numeric}
          min={field.constraints.ge ?? 1}
          max={field.constraints.le ?? 102400}
          aria-label={`${field.label} (precise MB)`}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") {
              onChange(field.env_default as EditValue);
              return;
            }
            const n = Number(raw);
            if (Number.isFinite(n)) onChange(n);
          }}
        />
      </div>
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
  // v1.10 — list[str] fields like preferred_audio_languages.
  // Operators type a comma-separated string; we render the
  // current state as chips above the input so they see the
  // tokenized result. The backend pre-coerces strings to lists
  // before validation, so we can send the raw string through
  // onChange and the round-trip stays consistent.
  if (field.type === "string_list") {
    const tokens = Array.isArray(value)
      ? (value as string[])
      : typeof value === "string"
        ? value
            .split(",")
            .map((s) => s.trim().toLowerCase())
            .filter(Boolean)
        : [];
    return (
      <div className="flex flex-col gap-1.5" style={{ maxWidth: 420 }}>
        {tokens.length > 0 ? (
          <div className="flex flex-wrap gap-1" data-testid="string-list-chips">
            {tokens.map((tok, i) => (
              <span
                key={i}
                className="inline-block px-1.5 py-0.5 rounded bg-surface-2 border border-border text-[11px] font-mono"
              >
                {tok}
              </span>
            ))}
          </div>
        ) : null}
        <Input
          type="text"
          variant="mono"
          value={tokens.join(", ")}
          placeholder="eng, fra, spa"
          onChange={(e) => {
            const raw = e.target.value;
            // Always send a comma-separated string up. The
            // backend pre-coerces to list[str]; the runtime
            // settings store also accepts list, but a string is
            // simpler at the edit surface and lossless on
            // whitespace-during-typing edge cases.
            onChange(raw);
          }}
          data-testid="string-list-input"
        />
        <span className="text-[10.5px] text-muted-2">
          Comma-separated. Three-letter ISO 639-2 codes (eng, fra, spa…).
        </span>
      </div>
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
