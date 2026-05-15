/**
 * Stage 6 — Integration health pill.
 *
 * Extracted from the inline ``HealthPill`` in ``IntegrationsPage``.
 * Three-state colour mapping: ok → green, degraded → yellow,
 * error → red. Everything else (``unknown``, future statuses) gets
 * a neutral pill.
 */

import { Pill } from "@/components/ui/Pill";
import type { Integration } from "@/hooks/useIntegrations";

export interface HealthPillProps {
  status: Integration["health_status"];
}

export function HealthPill({ status }: HealthPillProps) {
  const cls =
    status === "ok"
      ? "text-sev-ok border-sev-ok/40 bg-sev-ok/10"
      : status === "degraded"
        ? "text-sev-warn border-sev-warn/40 bg-sev-warn/10"
        : status === "error"
          ? "text-sev-error border-sev-error/40 bg-sev-error/10"
          : "";
  return <Pill className={cls}>{status}</Pill>;
}
