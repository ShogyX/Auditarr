/**
 * Stage 6 — Integrations page (slim orchestrator).
 *
 * Composes:
 *   - ``PageHeader``                      — title / subtitle
 *   - ``ConnectorDirectoryCard``          — pick a kind to add
 *   - ``ConfiguredIntegrationsCard``      — existing integrations
 *   - ``IntegrationConnectDialog``        — create (Stage 1 Modal)
 *
 * Stage 9 audit fix (Issue 13): the connect dialog now also serves
 * the edit flow. We keep two transient pieces of state:
 *   - ``connectingKind``    — set when the operator picked a new
 *                              connector kind from the directory
 *   - ``editingIntegration`` — set when the operator clicked Edit
 *                              on a configured row
 * Exactly one is set at a time; the dialog binds to whichever is
 * non-null. For the edit path, we resolve the matching IntegrationKind
 * from the kinds list so the dialog can re-use the schema-driven
 * inputs without re-querying.
 *
 * Pre-Stage-6:  556 LOC
 * Post-Stage-6: ~65 LOC (this file)
 */

import { useState } from "react";

import { PageHeader } from "@/components/shell/PageHeader";
import { useHelpKey } from "@/hooks/useHelpKey";
import {
  useDeleteIntegration,
  useIntegrationKinds,
  useIntegrations,
  useTriggerHealthcheck,
  useUpdateIntegration,
  type Integration,
  type IntegrationKind,
} from "@/hooks/useIntegrations";

import { ConfiguredIntegrationsCard } from "./ConfiguredIntegrationsCard";
import { ConnectorDirectoryCard } from "./ConnectorDirectoryCard";
import { IntegrationConnectDialog } from "./IntegrationConnectDialog";
import { VirusTotalCard } from "./VirusTotalCard";
// v1.9 Stage 2.1 — path-mappings editor moved here from
// Settings → Integrations. The Settings tab is gone; this is now
// the canonical home for the surface (and the page is the natural
// place: path mappings exist BECAUSE integrations exist).
import { PathMappingsPanel } from "@/features/settings/PathMappingsPanel";

export function IntegrationsPage() {
  useHelpKey("integrations.overview");

  const integrations = useIntegrations();
  const kinds = useIntegrationKinds();
  const remove = useDeleteIntegration();
  const update = useUpdateIntegration();
  const healthcheck = useTriggerHealthcheck();
  const [connectingKind, setConnectingKind] = useState<IntegrationKind | null>(
    null,
  );
  const [editingIntegration, setEditingIntegration] =
    useState<Integration | null>(null);

  // Resolve the kind metadata for the integration the operator is
  // editing. Returns null if the kinds list hasn't loaded or the
  // integration's kind isn't recognized — in either case we don't
  // render the dialog (the row's Edit button stays inert until the
  // kinds query resolves, which is usually within milliseconds of
  // the page load thanks to React Query caching).
  const editingKind: IntegrationKind | null = editingIntegration
    ? (kinds.data ?? []).find((k) => k.kind === editingIntegration.kind) ?? null
    : null;

  return (
    <>
      <PageHeader
        title="Integrations"
        sub="Connect to Plex, Sonarr, Radarr, and other services"
        helpKey="integrations.overview"
      />
      {/* v1.9 Stage 9.5.5 (OP-5) — drop the ``max-w-4xl`` cap
          and rearrange into a 2-column grid on xl. The page
          previously stacked four cards vertically in a 896px
          column on wide screens, leaving ~half the viewport
          unused.

          Layout shape:
            - ConnectorDirectoryCard spans both columns
              (rendered as a horizontal grid of connector kinds,
              wants full width)
            - ConfiguredIntegrationsCard spans both columns
              (table-like list of configured rows; rows would
              truncate at half-width)
            - VirusTotalCard + PathMappingsPanel sit side-by-
              side on xl (they're roughly equal weight) */}
      <div className="p-6 flex flex-col gap-6 max-w-4xl xl:max-w-none">
        <ConnectorDirectoryCard
          kinds={kinds.data ?? []}
          isLoading={kinds.isLoading}
          onPick={setConnectingKind}
        />

        <ConfiguredIntegrationsCard
          integrations={integrations}
          onCheck={(id) => healthcheck.mutate(id)}
          onEdit={(integration) => setEditingIntegration(integration)}
          onToggle={(integration) =>
            update.mutate({
              id: integration.id,
              patch: { enabled: !integration.enabled },
            })
          }
          onDelete={(integration) => {
            if (confirm(`Delete integration "${integration.name}"?`)) {
              remove.mutate(integration.id);
            }
          }}
        />

        {/* VirusTotal and Path Mappings are independent panels;
            on xl they sit side-by-side. */}
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
          {/* Stage 10 (v1.7) — VirusTotal quota + queue card. */}
          <VirusTotalCard />

          {/* v1.9 Stage 2.1 — path-mappings editor. Moved from
              Settings → Integrations sub-tab; this is now its only
              home. The panel surfaces every integration's
              ``config.path_mappings`` and the global mapping layer
              in one editor. */}
          <PathMappingsPanel />
        </div>
      </div>

      {connectingKind ? (
        <IntegrationConnectDialog
          kind={connectingKind}
          onClose={() => setConnectingKind(null)}
        />
      ) : null}

      {editingIntegration && editingKind ? (
        <IntegrationConnectDialog
          kind={editingKind}
          integration={editingIntegration}
          onClose={() => setEditingIntegration(null)}
        />
      ) : null}
    </>
  );
}
