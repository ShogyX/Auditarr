/**
 * Stage 6 — Schema-driven form input for integration connectors.
 *
 * Extracted from the inline ``DynamicInput`` in ``IntegrationsPage``.
 * Renders an appropriate control based on the JSON-Schema property
 * metadata: checkbox for boolean, number-input for integer, plain
 * text-input otherwise.
 *
 * v1.9 Stage 7.1 — array fields now route through structured
 * chip editors:
 *   * ``items.type === "object"`` with ``from``/``to`` properties
 *     → PathMappingEditor (per-row from/to inputs).
 *   * ``items.type === "string"`` → StringChipEditor (chip list).
 *   * Anything else falls back to the legacy textarea path so
 *     novel shapes don't regress.
 */

import { Input } from "@/components/ui/Input";
import { PathMappingEditor } from "@/features/integrations/PathMappingEditor";
import { StringChipEditor } from "@/features/integrations/StringChipEditor";
import type { IntegrationKind } from "@/hooks/useIntegrations";

export interface IntegrationDynamicInputProps {
  meta: NonNullable<IntegrationKind["config_schema"]["properties"]>[string];
  value: unknown;
  onChange: (v: unknown) => void;
  /** v1.9 Stage 7.1 — when present, the chip editors render
   *  their Auto-discover button. Each callback hits its
   *  corresponding backend probe and returns the suggestion
   *  list. ``fieldKey`` is the property name in the schema
   *  so the caller can dispatch the right probe per field
   *  (path_mappings → POST .../discover-path-mappings,
   *  source_whitelist → POST .../discover-webhook-sources,
   *  tag_allowlist / tag_denylist → GET .../upstream-tags). */
  fieldKey?: string;
  onAutoDiscoverPathMappings?: () => Promise<
    Array<{
      from: string;
      to: string;
      confidence: "high" | "medium" | "low" | "none";
      library_id: string | null;
      library_name: string | null;
    }>
  >;
  onAutoDiscoverWebhookSources?: () => Promise<string[]>;
  onAutoDiscoverTags?: () => Promise<string[]>;
}

export function IntegrationDynamicInput({
  meta,
  value,
  onChange,
  fieldKey,
  onAutoDiscoverPathMappings,
  onAutoDiscoverWebhookSources,
  onAutoDiscoverTags,
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
  if (meta.type === "array") {
    // v1.9 Stage 7.1 — discriminate on items.type. The legacy
    // textarea is the fallback when neither structured path
    // applies (preserves any schema shapes we didn't anticipate).
    const itemsMeta =
      meta.items && typeof meta.items === "object"
        ? (meta.items as { type?: string; properties?: Record<string, unknown> })
        : undefined;

    // Path mappings: items is an object with from/to.
    if (
      itemsMeta?.type === "object" &&
      itemsMeta.properties &&
      "from" in itemsMeta.properties &&
      "to" in itemsMeta.properties
    ) {
      const rows = Array.isArray(value)
        ? (value as Array<{ from: string; to: string }>)
        : [];
      return (
        <PathMappingEditor
          value={rows}
          onChange={(next) => onChange(next)}
          onAutoDiscover={onAutoDiscoverPathMappings}
        />
      );
    }

    // String chip list: items.type === "string".
    if (itemsMeta?.type === "string") {
      const items = Array.isArray(value)
        ? (value as unknown[]).map((v) => String(v))
        : [];
      // Wire the right discover callback per field name so the
      // operator picks suggestions from the right source.
      let onAutoDiscover: (() => Promise<string[]>) | undefined;
      let discoverLabel = "Auto-discover";
      let placeholder = "Add an entry…";
      if (fieldKey === "source_whitelist") {
        onAutoDiscover = onAutoDiscoverWebhookSources;
        discoverLabel = "From recent deliveries";
        placeholder = "192.168.1.0/24 or sonarr.local";
      } else if (
        fieldKey === "tag_allowlist" ||
        fieldKey === "tag_denylist"
      ) {
        onAutoDiscover = onAutoDiscoverTags;
        discoverLabel = "From upstream tags";
        placeholder = "tag name";
      }
      return (
        <StringChipEditor
          value={items}
          onChange={(next) => onChange(next)}
          placeholder={placeholder}
          onAutoDiscover={onAutoDiscover}
          discoverLabel={discoverLabel}
          ariaLabel={fieldKey}
        />
      );
    }

    // Legacy textarea fallback — preserves the Stage 11 shape
    // for any schema we didn't anticipate above.
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
        placeholder={"one entry per line"}
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
