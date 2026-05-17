/**
 * Stage 6 — Schema-driven form input for integration connectors.
 *
 * Extracted from the inline ``DynamicInput`` in ``IntegrationsPage``.
 * Renders an appropriate control based on the JSON-Schema property
 * metadata: checkbox for boolean, number-input for integer, plain
 * text-input otherwise.
 *
 * Uses the Stage 1 ``Input`` primitive for text/integer fields
 * instead of the feature-local ``Input`` shadow that this stage
 * retires.
 *
 * Note: Notifications has its own ``DynamicInput`` that also handles
 * ``enum`` properties (rendering a select). The two are not unified
 * yet because each understands its own schema shape and a deeper
 * schema-driven form component is queued for Stage 6b. Sharing them
 * prematurely would force a less natural API on both sides.
 */

import { Input } from "@/components/ui/Input";
import type { IntegrationKind } from "@/hooks/useIntegrations";

export interface IntegrationDynamicInputProps {
  meta: NonNullable<IntegrationKind["config_schema"]["properties"]>[string];
  value: unknown;
  onChange: (v: unknown) => void;
}

export function IntegrationDynamicInput({
  meta,
  value,
  onChange,
}: IntegrationDynamicInputProps) {
  if (meta.type === "boolean") {
    return (
      <input
        type="checkbox"
        checked={Boolean(value)}
        onChange={(e) => onChange(e.target.checked)}
        className="h-4 w-4 self-start"
      />
    );
  }
  if (meta.type === "integer") {
    return (
      <Input
        type="number"
        value={typeof value === "number" ? value : ""}
        min={meta.minimum}
        max={meta.maximum}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    );
  }
  // Stage 11 (plan §549) — string-array fields like
  // ``source_whitelist``. Rendered as a textarea with one
  // entry per line — matches how operators think about IP /
  // CIDR / hostname lists and avoids the need for a more
  // elaborate tag-pill editor. The value sent back to the
  // server is a list of trimmed non-empty lines.
  if (meta.type === "array") {
    const lines = Array.isArray(value)
      ? value.map((v) => String(v))
      : [];
    return (
      <textarea
        className="min-h-[72px] w-full resize-y rounded-md border border-border bg-surface px-2 py-1 text-[13px] font-mono"
        value={lines.join("\n")}
        onChange={(e) =>
          onChange(
            e.target.value
              .split("\n")
              .map((s) => s.trim())
              .filter(Boolean),
          )
        }
        placeholder={
          "192.168.1.0/24\nsonarr.local\n10.0.0.5"
        }
        data-testid="integration-array-input"
      />
    );
  }
  return (
    <Input
      type="text"
      value={typeof value === "string" ? value : ""}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}
