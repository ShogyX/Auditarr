/**
 * Stage 6 — Integration discovery panel.
 *
 * Extracted from the inline ``DiscoverPanel`` in ``IntegrationsPage``.
 * Renders the expandable "Discovered libraries" disclosure inside an
 * integration row — operator clicks Discover, we ask the upstream
 * for its library list, each entry gets a Promote button that
 * creates a managed library bound to that root path.
 */

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Tag } from "@/components/ui/Pill";
import {
  useDiscoverLibraries,
  type DiscoveredLibraryEntry,
  type Integration,
} from "@/hooks/useIntegrations";
import { useCreateLibrary } from "@/hooks/useMedia";

export interface IntegrationDiscoverPanelProps {
  integration: Integration;
}

export function IntegrationDiscoverPanel({
  integration,
}: IntegrationDiscoverPanelProps) {
  const discover = useDiscoverLibraries();
  const createLibrary = useCreateLibrary();
  const [libs, setLibs] = useState<DiscoveredLibraryEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setError(null);
    try {
      const result = await discover.mutateAsync(integration.id);
      setLibs(result);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="px-12 py-3 border-b border-border last:border-b-0 bg-surface-sunk">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[12px] font-semibold text-text-2">
          Discovered libraries
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={run}
          disabled={discover.isPending}
        >
          <Icon name="refresh" size={12} />
          <span className="ml-1">
            {discover.isPending ? "Scanning…" : "Discover"}
          </span>
        </Button>
      </div>
      {error ? (
        <div className="text-[12px] text-sev-error">{error}</div>
      ) : libs == null ? (
        <div className="text-[12px] text-muted">
          Click <em>Discover</em> to ask {integration.kind} for its libraries.
        </div>
      ) : libs.length === 0 ? (
        <div className="text-[12px] text-muted">
          No libraries reported by this integration.
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {libs.map((lib) => (
            <div
              key={`${lib.upstream_id}:${lib.name}`}
              className="flex items-center gap-3 px-3 py-2 bg-surface border border-border rounded-md"
            >
              <Icon name="folder" size={14} className="text-muted-2" />
              <div className="min-w-0 flex-1">
                <div className="text-[12.5px] font-medium truncate">
                  {lib.name}
                </div>
                <div className="text-[11px] font-mono text-muted truncate">
                  {lib.root_path ?? "(no path reported)"}
                </div>
              </div>
              <Tag>{lib.kind}</Tag>
              <Button
                size="sm"
                variant="ghost"
                disabled={!lib.root_path || createLibrary.isPending}
                onClick={() =>
                  createLibrary.mutate({
                    name: lib.name,
                    root_path: lib.root_path!,
                    kind: lib.kind,
                  })
                }
                title={
                  lib.root_path
                    ? "Promote to managed library"
                    : "No root path; cannot promote"
                }
              >
                <Icon name="plus" size={12} />
                <span className="ml-1">Promote</span>
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
