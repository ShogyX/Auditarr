/**
 * Stage 6 — Integrations shared helpers.
 *
 * Single source of truth for the small bits previously inlined in
 * ``IntegrationsPage.tsx``.
 *
 * ``initialConfig`` reads a connector's JSON-Schema and yields a
 * mostly-blank config dict pre-populated with declared defaults.
 * Mirrors the equivalent helper in Notifications; the two stay
 * separate for now because they accept different ``Kind`` types and
 * a deeper schema-driven shared component is queued for Stage 6b.
 */

import type { IntegrationKind } from "@/hooks/useIntegrations";

export function initialConfig(kind: IntegrationKind): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, meta] of Object.entries(kind.config_schema.properties ?? {})) {
    if (meta.default !== undefined) out[key] = meta.default;
    else if (meta.type === "boolean") out[key] = false;
    else if (meta.type === "integer") out[key] = 0;
    else out[key] = "";
  }
  return out;
}
