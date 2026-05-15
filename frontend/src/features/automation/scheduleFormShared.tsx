/**
 * Schedule form primitives — shared between create + edit dialogs.
 *
 * Stage 9 audit follow-up. The Stage 5 schedule-create dialog
 * shipped these widgets inline; when the Stage 9 follow-up added an
 * edit dialog, they were lifted here so both callers use the same
 * structured-form pattern.
 *
 * Exports:
 *   - ``ArgInput``       — one labeled input per ``args_schema``
 *                          property (string / number / boolean / enum)
 *   - ``CronFieldset``   — the preset + five-field cron editor
 *   - ``buildCronPayload`` — `CronState` → numeric dict for POST
 *   - ``parseCronToState`` — server cron dict → `CronState`
 *   - ``initialArgsFor`` — pre-populate args from a JobKind's defaults
 *   - ``PRESET_CRON`` / ``CronPreset`` / ``CronState`` (types + presets)
 */

import { type ReactNode, useEffect } from "react";

import { Field } from "@/components/ui/Field";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { useIntegrations } from "@/hooks/useIntegrations";
import { useLibraries, useTagsCatalog } from "@/hooks/useMedia";
import { cn } from "@/lib/cn";
import type { JobKind } from "@/hooks/useAutomation";

// ── Cron types + presets ───────────────────────────────────────
export type CronPreset = "daily" | "weekly" | "monthly" | "custom";

export interface CronState {
  minute: string; // "" = unset (means "any")
  hour: string;
  day: string;
  month: string;
  weekday: string;
}

export const EMPTY_CRON: CronState = {
  minute: "",
  hour: "",
  day: "",
  month: "",
  weekday: "",
};

export const PRESET_CRON: Record<Exclude<CronPreset, "custom">, CronState> = {
  // Run once a day at 03:00.
  daily: { minute: "0", hour: "3", day: "", month: "", weekday: "" },
  // Run weekly on Sundays at 03:00 (weekday=6 in 0=Mon convention).
  weekly: { minute: "0", hour: "3", day: "", month: "", weekday: "6" },
  // Run on the 1st of every month at 03:00.
  monthly: { minute: "0", hour: "3", day: "1", month: "", weekday: "" },
};

// ── Helpers ────────────────────────────────────────────────────
/** Backend cron-spec dict → form state. The backend stores ints or
 *  lists of ints under minute / hour / day / month / weekday. We
 *  surface ints as their string repr; lists are surfaced as the
 *  comma-joined repr (visible to operators in the input; submission
 *  reparses them). Unknown / missing keys become empty strings. */
export function parseCronToState(
  cron: Record<string, unknown> | null | undefined,
): CronState {
  const out: CronState = { ...EMPTY_CRON };
  if (!cron) return out;
  for (const key of ["minute", "hour", "day", "month", "weekday"] as const) {
    const v = cron[key];
    if (v == null) continue;
    if (typeof v === "number") {
      out[key] = String(v);
    } else if (Array.isArray(v)) {
      // Best effort — first value wins for the simple form. Lists
      // are rare in practice; an operator who set ``minute=[0, 30]``
      // via API will see the first value here and the JSON peek will
      // reveal the actual stored shape.
      const first = v[0];
      out[key] = typeof first === "number" ? String(first) : "";
    } else if (typeof v === "string") {
      out[key] = v;
    }
  }
  return out;
}

/** Form state → backend cron dict. Only non-empty fields are
 *  included so the backend's "absent = any" semantics works. */
export function buildCronPayload(state: CronState): Record<string, number> {
  const out: Record<string, number> = {};
  for (const key of ["minute", "hour", "day", "month", "weekday"] as const) {
    const raw = state[key].trim();
    if (raw === "") continue;
    const n = parseInt(raw, 10);
    if (Number.isFinite(n)) out[key] = n;
  }
  return out;
}

/** Build initial argument values from a JobKind's ``args_schema``
 *  defaults. Used both at create-time (no existing data) and at
 *  edit-time as a fallback when the saved schedule's job_args don't
 *  carry every property the current schema defines. */
export function initialArgsFor(
  kind: JobKind | undefined,
): Record<string, unknown> {
  if (!kind) return {};
  const out: Record<string, unknown> = {};
  const props = kind.args_schema?.properties ?? {};
  for (const [key, spec] of Object.entries(props)) {
    if (spec.default !== undefined) {
      out[key] = spec.default;
    }
  }
  return out;
}

// ── ArgInput ───────────────────────────────────────────────────
/**
 * Render one labeled input for a single property in an
 * ``args_schema``. Dispatches by ``type`` / ``enum`` to:
 *   - ``Select`` for enum properties
 *   - checkbox for booleans
 *   - ``<Input type="number">`` for numbers / integers
 *   - text ``Input`` otherwise
 */
export function ArgInput({
  argKey,
  spec,
  required,
  value,
  onChange,
}: {
  argKey: string;
  spec: {
    type?: string;
    title?: string;
    description?: string;
    default?: unknown;
    enum?: unknown[];
    /** Stage 17 (audit follow-up): semantic hint — ``library_id``
     *  renders a dropdown of every Library, ``integration_id``
     *  renders a dropdown of every Integration. Unset = fall through
     *  to the generic type/enum branches below. */
    format?: string;
  };
  required: boolean;
  value: unknown;
  onChange: (next: unknown) => void;
}): ReactNode {
  const label = `${spec.title ?? argKey}${required ? " *" : ""}`;
  const description = spec.description;

  // Stage 17 (audit follow-up): library_id / integration_id
  // dropdowns. We render them BEFORE the enum branch because they
  // are a kind of dynamic-enum — the source is the API, not the
  // schema itself. The Select shows the human label and submits the
  // id; falls back gracefully to a free-text input if the relevant
  // list isn't loaded yet (e.g. the hook is still pending).
  if (spec.format === "library_id") {
    return <LibrarySelectField label={label} description={description} required={required} value={value} onChange={onChange} />;
  }
  if (spec.format === "integration_id") {
    return <IntegrationSelectField label={label} description={description} required={required} value={value} onChange={onChange} />;
  }
  if (spec.format === "tag_list") {
    return <TagListField label={label} description={description} value={value} onChange={onChange} />;
  }

  // Enum → select. Coerce values to string for <option> compat.
  if (spec.enum && spec.enum.length > 0) {
    return (
      <Field label={label}>
        <Select
          value={value == null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value)}
          required={required}
        >
          {!required ? <option value="">— select —</option> : null}
          {spec.enum.map((opt) => (
            <option key={String(opt)} value={String(opt)}>
              {String(opt)}
            </option>
          ))}
        </Select>
        {description ? (
          <span className="text-[11px] text-muted-2">{description}</span>
        ) : null}
      </Field>
    );
  }

  // Boolean → checkbox.
  if (spec.type === "boolean") {
    return (
      <Field label={label}>
        <label className="flex items-center gap-2 text-[12.5px]">
          <input
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => onChange(e.target.checked)}
          />
          {description ? (
            <span className="text-muted-2">{description}</span>
          ) : (
            <span className="text-muted-2">Enable {argKey}</span>
          )}
        </label>
      </Field>
    );
  }

  // Number / integer → number input.
  if (spec.type === "number" || spec.type === "integer") {
    return (
      <Field label={label}>
        <Input
          type="number"
          required={required}
          value={value == null ? "" : String(value)}
          step={spec.type === "integer" ? 1 : "any"}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") {
              onChange(undefined);
              return;
            }
            const n =
              spec.type === "integer" ? parseInt(raw, 10) : Number(raw);
            onChange(Number.isFinite(n) ? n : undefined);
          }}
        />
        {description ? (
          <span className="text-[11px] text-muted-2">{description}</span>
        ) : null}
      </Field>
    );
  }

  // Default: string input.
  return (
    <Field label={label}>
      <Input
        required={required}
        value={value == null ? "" : String(value)}
        onChange={(e) => onChange(e.target.value)}
        placeholder={spec.default != null ? String(spec.default) : undefined}
      />
      {description ? (
        <span className="text-[11px] text-muted-2">{description}</span>
      ) : null}
    </Field>
  );
}

// ── CronFieldset ───────────────────────────────────────────────
/**
 * Preset selector + the five cron number inputs as a unit. Editing
 * any of the five inputs automatically flips the preset to "custom"
 * so the indicator never lies.
 */
export function CronFieldset({
  preset,
  cron,
  onPresetChange,
  onCronChange,
}: {
  preset: CronPreset;
  cron: CronState;
  onPresetChange: (next: CronPreset) => void;
  onCronChange: (next: CronState) => void;
}) {
  // When the preset changes (and isn't "custom"), pour the preset
  // values into the cron form. ``custom`` is the manual-edit mode
  // and intentionally doesn't overwrite.
  useEffect(() => {
    if (preset !== "custom") {
      onCronChange(PRESET_CRON[preset]);
    }
    // ``onCronChange`` is intentionally omitted from deps — the
    // caller passes a stable setter; including it would re-run the
    // effect every render and overwrite local edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset]);

  function patchCron(key: keyof CronState, value: string) {
    onCronChange({ ...cron, [key]: value });
    // Any manual edit means we're in custom territory now.
    onPresetChange("custom");
  }

  return (
    <fieldset
      className={cn(
        "flex flex-col gap-2 p-3 rounded-md",
        "border border-border bg-surface-sunk",
      )}
    >
      <legend className="px-1.5 text-[11.5px] text-muted-2 font-medium">
        Schedule
      </legend>
      <Field label="Preset">
        <Select
          value={preset}
          onChange={(e) => onPresetChange(e.target.value as CronPreset)}
        >
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
          <option value="monthly">Monthly</option>
          <option value="custom">Custom</option>
        </Select>
        <span className="text-[11px] text-muted-2">
          Leave a field blank for &quot;any&quot;. Weekday: 0=Mon … 6=Sun.
        </span>
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Hour (0–23)">
          <Input
            type="number"
            min={0}
            max={23}
            value={cron.hour}
            onChange={(e) => patchCron("hour", e.target.value)}
          />
        </Field>
        <Field label="Minute (0–59)">
          <Input
            type="number"
            min={0}
            max={59}
            value={cron.minute}
            onChange={(e) => patchCron("minute", e.target.value)}
          />
        </Field>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <Field label="Day (1–31)">
          <Input
            type="number"
            min={1}
            max={31}
            value={cron.day}
            onChange={(e) => patchCron("day", e.target.value)}
          />
        </Field>
        <Field label="Month (1–12)">
          <Input
            type="number"
            min={1}
            max={12}
            value={cron.month}
            onChange={(e) => patchCron("month", e.target.value)}
          />
        </Field>
        <Field label="Weekday (0–6)">
          <Input
            type="number"
            min={0}
            max={6}
            value={cron.weekday}
            onChange={(e) => patchCron("weekday", e.target.value)}
          />
        </Field>
      </div>
    </fieldset>
  );
}

// ── Stage 17 (audit follow-up): reference-field helpers ──────────

/** Dropdown of every Library, submitting the library id. Used when
 *  a schema field declares ``format: "library_id"`` (see
 *  ``backend/app/automation/jobs.py``). Falls back to a text input
 *  if the libraries list hasn't loaded yet so the form stays usable
 *  during the brief pending window. */
function LibrarySelectField({
  label,
  description,
  required,
  value,
  onChange,
}: {
  label: string;
  description: string | undefined;
  required: boolean;
  value: unknown;
  onChange: (next: unknown) => void;
}): ReactNode {
  const libraries = useLibraries();
  if (!libraries.data) {
    return (
      <Field label={label}>
        <Input
          value={value == null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value)}
          required={required}
          placeholder="Library id"
        />
        {description ? (
          <span className="text-[11px] text-muted-2">{description}</span>
        ) : null}
      </Field>
    );
  }
  return (
    <Field label={label}>
      <Select
        value={value == null ? "" : String(value)}
        onChange={(e) => onChange(e.target.value)}
        required={required}
        aria-label={label}
      >
        {!required || value == null ? (
          <option value="">— select a library —</option>
        ) : null}
        {libraries.data.map((lib) => (
          <option key={lib.id} value={lib.id}>
            {lib.name}
          </option>
        ))}
      </Select>
      {description ? (
        <span className="text-[11px] text-muted-2">{description}</span>
      ) : null}
    </Field>
  );
}

/** Same shape as ``LibrarySelectField`` but populated from
 *  ``useIntegrations()``. Used when a schema field declares
 *  ``format: "integration_id"``. */
function IntegrationSelectField({
  label,
  description,
  required,
  value,
  onChange,
}: {
  label: string;
  description: string | undefined;
  required: boolean;
  value: unknown;
  onChange: (next: unknown) => void;
}): ReactNode {
  const integrations = useIntegrations();
  if (!integrations.data) {
    return (
      <Field label={label}>
        <Input
          value={value == null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value)}
          required={required}
          placeholder="Integration id"
        />
        {description ? (
          <span className="text-[11px] text-muted-2">{description}</span>
        ) : null}
      </Field>
    );
  }
  return (
    <Field label={label}>
      <Select
        value={value == null ? "" : String(value)}
        onChange={(e) => onChange(e.target.value)}
        required={required}
        aria-label={label}
      >
        {!required || value == null ? (
          <option value="">— select an integration —</option>
        ) : null}
        {integrations.data.map((ig) => (
          <option key={ig.id} value={ig.id}>
            {ig.name} ({ig.kind})
          </option>
        ))}
      </Select>
      {description ? (
        <span className="text-[11px] text-muted-2">{description}</span>
      ) : null}
    </Field>
  );
}

/** Stage 18 (audit follow-up): tag-scope chip-input. Renders the
 *  currently-selected tags as removable chips plus a Select of
 *  every-not-yet-picked tag from /tags. Operators can also type a
 *  tag name and press Enter to add a tag that doesn't exist yet
 *  (rare — but supported because tag catalog updates are eventually
 *  consistent after a fresh integration sync). */
function TagListField({
  label,
  description,
  value,
  onChange,
}: {
  label: string;
  description: string | undefined;
  value: unknown;
  onChange: (next: unknown) => void;
}): ReactNode {
  const catalog = useTagsCatalog();
  const selected: string[] = Array.isArray(value)
    ? value.map(String)
    : [];
  const available = (catalog.data ?? []).filter(
    (t) => !selected.includes(t),
  );

  const addTag = (tag: string) => {
    const trimmed = tag.trim();
    if (!trimmed || selected.includes(trimmed)) return;
    onChange([...selected, trimmed]);
  };
  const removeTag = (tag: string) => {
    onChange(selected.filter((t) => t !== tag));
  };

  return (
    <Field label={label}>
      <div
        className="flex flex-wrap gap-1 items-center"
        data-testid="tag-list-input"
      >
        {selected.map((tag) => (
          <span
            key={tag}
            className="inline-flex items-center gap-1 px-2 py-0.5 text-[11.5px] rounded-md bg-surface-2 border border-default"
            data-testid="tag-chip"
          >
            {tag}
            <button
              type="button"
              className="text-muted-2 hover:text-default"
              onClick={() => removeTag(tag)}
              aria-label={`Remove tag ${tag}`}
              title="Remove"
            >
              ×
            </button>
          </span>
        ))}
        {available.length > 0 ? (
          <Select
            value=""
            aria-label={`Add tag to ${label}`}
            onChange={(e) => {
              if (e.target.value) {
                addTag(e.target.value);
                // Reset so picking the same option twice still
                // fires onChange.
                e.currentTarget.value = "";
              }
            }}
          >
            <option value="">— add tag —</option>
            {available.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </Select>
        ) : null}
        <Input
          type="text"
          placeholder="Type then Enter"
          aria-label={`Add custom tag to ${label}`}
          className="w-32"
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addTag((e.target as HTMLInputElement).value);
              (e.target as HTMLInputElement).value = "";
            }
          }}
        />
      </div>
      {description ? (
        <span className="text-[11px] text-muted-2">{description}</span>
      ) : null}
    </Field>
  );
}
