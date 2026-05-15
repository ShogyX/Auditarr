/**
 * Stage 4 — Rule editor dry-run panel.
 *
 * Lets the operator evaluate the current rule definition against a
 * real media file without saving the rule. Extracted verbatim from
 * the inline ``DryRunPanel`` at the bottom of ``RuleEditorPage.tsx``;
 * the panel was already isolated logically, just not file-wise.
 *
 * The panel intentionally fetches its own ``useMediaList(limit: 25)``
 * rather than receiving the file list from the parent — different
 * editor sessions don't need to share the file picker state, and
 * caching is handled by React Query.
 */

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Pill, Tag } from "@/components/ui/Pill";
import { useMediaList } from "@/hooks/useMedia";
import {
  useDryRunRule,
  type DryRunResult,
  type RuleDefinition,
} from "@/hooks/useRules";
import { cn } from "@/lib/cn";

export interface DryRunPanelProps {
  definition: RuleDefinition;
}

export function DryRunPanel({ definition }: DryRunPanelProps) {
  const dryRun = useDryRunRule();
  const media = useMediaList({ limit: 25 });
  const [selectedFileId, setSelectedFileId] = useState<string>("");
  const [result, setResult] = useState<DryRunResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function runDryRun() {
    setError(null);
    setResult(null);
    if (!selectedFileId) {
      setError("Pick a media file to test the rule against.");
      return;
    }
    try {
      const out = await dryRun.mutateAsync({
        definition,
        media_file_id: selectedFileId,
      });
      setResult(out);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="text-[12px] text-muted-2">
        Evaluate this rule against an existing file without saving. The result
        shows what severity/tags/optimizations the rule would apply.
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <label className="flex items-center gap-2 flex-1 min-w-[280px]">
          <span className="text-[11.5px] text-muted-2 whitespace-nowrap">
            Test against
          </span>
          <select
            value={selectedFileId}
            onChange={(e) => setSelectedFileId(e.target.value)}
            className="settings-input flex-1"
          >
            <option value="">Pick a file…</option>
            {(media.data?.items ?? []).map((m) => (
              <option key={m.id} value={m.id}>
                {m.filename} {m.severity ? `· ${m.severity}` : ""}
              </option>
            ))}
          </select>
        </label>
        <Button
          variant="accent"
          size="sm"
          onClick={runDryRun}
          disabled={!selectedFileId || dryRun.isPending}
        >
          <Icon name="play" size={12} />
          <span className="ml-1">
            {dryRun.isPending ? "Running…" : "Run dry-run"}
          </span>
        </Button>
      </div>

      {error ? <div className="text-[12px] text-sev-error">{error}</div> : null}

      {result ? (
        <div className="border border-border rounded-md p-3 bg-surface-2 flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <span className="text-[11.5px] uppercase tracking-[0.06em] text-muted-2 font-semibold">
              Result
            </span>
            <Pill
              className={cn(
                result.matched
                  ? "text-sev-ok border-sev-ok/40 bg-sev-ok/10"
                  : "text-muted-2 border-border bg-surface-sunk",
              )}
            >
              {result.matched ? "matched" : "did not match"}
            </Pill>
          </div>
          {result.matched ? (
            <>
              <div className="text-[12.5px]">
                <span className="text-muted-2">would set severity:</span>{" "}
                <span className="font-mono">{result.severity ?? "—"}</span>{" "}
                <span className="text-muted-2">
                  (rank {result.severity_rank})
                </span>
              </div>
              {result.add_tags.length > 0 ? (
                <div className="text-[12.5px]">
                  <span className="text-muted-2">would add tags:</span>{" "}
                  {result.add_tags.map((t) => (
                    <Tag key={t}>{t}</Tag>
                  ))}
                </div>
              ) : null}
              {result.queue_optimizations.length > 0 ? (
                <div className="text-[12.5px]">
                  <span className="text-muted-2">
                    would queue optimizations:
                  </span>{" "}
                  {result.queue_optimizations.map((p) => (
                    <Tag key={p}>{p}</Tag>
                  ))}
                </div>
              ) : null}
            </>
          ) : (
            <div className="text-[12.5px] text-muted-2">
              The conditions did not match this file's attributes.
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
