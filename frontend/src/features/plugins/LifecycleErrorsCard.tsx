/**
 * Stage 6 — Plugins lifecycle-errors panel.
 *
 * Extracted from the inline ``LifecycleErrorsCard`` in
 * ``PluginsPage.tsx``. Renders at the bottom of the page when one or
 * more plugins have an ``errored`` or ``failed_to_load`` status.
 *
 * The "isolated — host continues" tag reminds the operator that
 * lifecycle errors don't crash the host; the panel is informational,
 * not actionable beyond what the plugin row's Reload button already
 * offers.
 */

import { Card, CardBody } from "@/components/ui/Card";
import type { PluginSummary } from "@/hooks/usePlugins";
import { cn } from "@/lib/cn";

import { StatusPill } from "./pluginsShared";

export interface LifecycleErrorsCardProps {
  plugins: PluginSummary[];
}

export function LifecycleErrorsCard({ plugins }: LifecycleErrorsCardProps) {
  return (
    <Card>
      <CardBody>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-[13px] font-semibold m-0">
            Lifecycle errors{" "}
            <span className="text-muted font-normal text-[11.5px]">
              {plugins.length}
            </span>
          </h3>
          <span className="text-[11.5px] text-muted-2">
            isolated — host continues
          </span>
        </div>
        <ul className="m-0 p-0 list-none">
          {plugins.map((p) => (
            <li
              key={p.id}
              className={cn(
                "py-2 border-t border-border first:border-t-0",
                "flex flex-col gap-1",
              )}
            >
              <div className="flex items-center gap-2 flex-wrap">
                <StatusPill status={p.status ?? "errored"} />
                <span className="text-[13px] font-medium">{p.name}</span>
                <span className="text-[11px] text-muted-2 font-mono">
                  {p.id}@{p.version}
                </span>
              </div>
              {p.last_error ? (
                <pre className="m-0 text-[11.5px] font-mono text-sev-error whitespace-pre-wrap">
                  {p.last_error}
                </pre>
              ) : null}
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}
