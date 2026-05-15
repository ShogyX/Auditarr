/**
 * Stage 16: review modal for a rule suggestion.
 *
 * Opens with three tabs:
 *   - Visual: the Stage 15 ``VisualRuleBuilder`` pre-populated with
 *     the analyzer's drafted definition. Edits go through to the
 *     deploy mutation as a ``definition_override``.
 *   - Evidence: structured render of the suggestion's ``evidence``
 *     JSON — the actual playback patterns that produced the
 *     recommendation. Helps the operator decide whether the
 *     analyzer's read is correct.
 *   - JSON: same JSON-text view as the rule editor — for the cases
 *     where the visual builder doesn't cover what the user wants.
 *
 * Saves close the modal via the parent's ``onClose``. Dismiss does
 * the same.
 */

import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Pill } from "@/components/ui/Pill";
import { ErrorState, LoadingState } from "@/components/ui/States";
import {
  useDeploySuggestion,
  useDismissSuggestion,
  useRuleVocabulary,
  type RuleDefinition,
  type RuleSuggestion,
} from "@/hooks/useRules";
import { cn } from "@/lib/cn";
import { fmtNum } from "@/lib/format";

import { VisualRuleBuilder } from "../rules/VisualRuleBuilder";

type Tab = "visual" | "evidence" | "json";

const HEURISTIC_LABEL: Record<string, string> = {
  high_transcode_codec: "Transcode codec",
  bitrate_ceiling: "Bitrate ceiling",
  container_compat: "Container compatibility",
  resolution_mismatch: "Resolution mismatch",
  failed_playback: "Failed playback",
};

export function SuggestionReviewModal({
  suggestion,
  onClose,
}: {
  suggestion: RuleSuggestion;
  onClose: () => void;
}) {
  const vocabulary = useRuleVocabulary();
  const deploy = useDeploySuggestion();
  const dismiss = useDismissSuggestion();
  const [tab, setTab] = useState<Tab>("visual");
  const [name, setName] = useState(suggestion.name);
  const [definition, setDefinition] = useState<RuleDefinition>(suggestion.definition);
  const [definitionText, setDefinitionText] = useState(() =>
    JSON.stringify(suggestion.definition, null, 2),
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const parsed = useMemo<{ ok: boolean; value?: RuleDefinition; error?: string }>(() => {
    try {
      return { ok: true, value: JSON.parse(definitionText) as RuleDefinition };
    } catch (err) {
      return { ok: false, error: (err as Error).message };
    }
  }, [definitionText]);

  function commitFromVisual(next: RuleDefinition) {
    setDefinition(next);
    setDefinitionText(JSON.stringify(next, null, 2));
  }

  function commitFromJson(text: string) {
    setDefinitionText(text);
    try {
      setDefinition(JSON.parse(text) as RuleDefinition);
    } catch {
      // Keep the last good typed definition; the visual tab won't
      // re-render until parse recovers.
    }
  }

  async function onDeploy() {
    setError(null);
    if (!parsed.ok) {
      setError(`Invalid JSON: ${parsed.error}`);
      return;
    }
    try {
      await deploy.mutateAsync({
        id: suggestion.id,
        patch: {
          name: name !== suggestion.name ? name : undefined,
          definition: parsed.value ?? definition,
        },
      });
      onClose();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function onDismiss() {
    if (
      !confirm("Dismiss this suggestion? Auditarr won't suggest this pattern again for 30 days.")
    ) {
      return;
    }
    try {
      await dismiss.mutateAsync({ id: suggestion.id });
      onClose();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const confidencePct = Math.round(suggestion.confidence * 100);

  return (
    <div
      className="fixed inset-0 z-40 bg-black/40 flex items-center justify-center p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bg-surface border border-border rounded-[var(--radius)] shadow-xl w-full max-w-5xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-4 h-11 border-b border-border shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <Pill className="text-[10px] text-muted-2 border-border bg-surface-2 shrink-0">
              {HEURISTIC_LABEL[suggestion.heuristic] ?? suggestion.heuristic}
            </Pill>
            <h3 className="text-[13px] font-semibold m-0 truncate">Review suggestion</h3>
          </div>
          <button onClick={onClose} className="text-muted-2 hover:text-text" aria-label="Close">
            <Icon name="x" size={14} />
          </button>
        </div>

        {/* Body */}
        <div className="p-4 flex flex-col gap-3 overflow-y-auto">
          {/* Editable name + projection row */}
          <div className="flex flex-col gap-1.5">
            <span className="text-[10.5px] uppercase tracking-[0.06em] text-muted-2 font-semibold">
              Suggested rule name
            </span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="h-8 px-2 text-[13px] bg-surface-2 border border-border rounded-md focus:outline-none focus:border-border-strong focus:ring-2 focus:ring-accent"
            />
          </div>

          <div className="flex items-center gap-4 text-[12px] border-y border-border py-2">
            <Stat label="Files affected" value={fmtNum(suggestion.files_affected)} />
            {suggestion.est_runtime_s != null ? (
              <Stat label="Est. runtime" value={fmtRuntime(suggestion.est_runtime_s)} />
            ) : null}
            <Stat label="Confidence" value={`${confidencePct}%`} />
          </div>

          {/* Tabs */}
          <div className="flex items-center gap-1 border-b border-border -mx-4 px-4">
            {(
              [
                ["visual", "Visual"],
                ["evidence", "Evidence"],
                ["json", "JSON"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setTab(key)}
                className={cn(
                  "px-3 h-8 text-[12.5px] border-b-2 -mb-px",
                  tab === key
                    ? "border-text text-text font-medium"
                    : "border-transparent text-muted-2 hover:text-text-2",
                )}
              >
                {label}
              </button>
            ))}
            <div className="flex-1" />
            {!parsed.ok ? (
              <span className="text-[11.5px] text-sev-error mr-2">{parsed.error}</span>
            ) : null}
          </div>

          {tab === "visual" ? (
            vocabulary.isLoading ? (
              <LoadingState label="Loading vocabulary…" />
            ) : vocabulary.isError || !vocabulary.data ? (
              <ErrorState
                title="Couldn't load rule vocabulary"
                description={(vocabulary.error as Error)?.message}
              />
            ) : (
              <VisualRuleBuilder
                definition={definition}
                vocabulary={vocabulary.data}
                onChange={commitFromVisual}
              />
            )
          ) : null}

          {tab === "evidence" ? <EvidenceView evidence={suggestion.evidence} /> : null}

          {tab === "json" ? (
            <textarea
              value={definitionText}
              onChange={(e) => commitFromJson(e.target.value)}
              spellCheck={false}
              rows={16}
              className={cn(
                "px-2 py-2 text-[12.5px] font-mono bg-surface-sunk border rounded-md",
                "focus:outline-none focus:ring-2 focus:ring-accent resize-y",
                parsed.ok ? "border-border" : "border-sev-error",
              )}
            />
          ) : null}

          {error ? <div className="text-[12px] text-sev-error">{error}</div> : null}
        </div>

        {/* Footer */}
        <div className="flex justify-end items-center gap-2 px-4 h-12 border-t border-border shrink-0">
          <Button variant="ghost" onClick={onDismiss} disabled={dismiss.isPending}>
            <Icon name="x" size={12} />
            <span className="ml-1">Dismiss</span>
          </Button>
          <div className="flex-1" />
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={onDeploy} disabled={deploy.isPending || !parsed.ok}>
            <Icon name="check" size={12} />
            <span className="ml-1">{deploy.isPending ? "Deploying…" : "Deploy as rule"}</span>
          </Button>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-[0.06em] text-muted-2">{label}</span>
      <span className="text-[14px] font-mono">{value}</span>
    </div>
  );
}

// ── Evidence renderer ───────────────────────────────────────
// Each heuristic shapes its evidence dict differently. We render the
// well-known keys nicely and dump the rest as a fallback JSON block
// so the UI doesn't silently hide useful diagnostic info.
function EvidenceView({ evidence }: { evidence: Record<string, unknown> }) {
  // Common shape across heuristics: a counts object + a small sample
  // of representative playback events.
  const counters = pickRecord(evidence, [
    "total_plays",
    "transcoded_plays",
    "failed_plays",
    "direct_plays",
    "matched_files",
    "affected_devices",
    "max_bitrate_kbps",
    "median_bitrate_kbps",
  ]);
  const sample = Array.isArray(evidence.sample)
    ? (evidence.sample as Array<Record<string, unknown>>)
    : [];

  return (
    <div className="flex flex-col gap-3 text-[12.5px]">
      {Object.keys(counters).length > 0 ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
          {Object.entries(counters).map(([k, v]) => (
            <div key={k} className="p-2 border border-border rounded-md bg-surface-2">
              <div className="text-[10px] uppercase tracking-[0.06em] text-muted-2">
                {k.replace(/_/g, " ")}
              </div>
              <div className="font-mono text-[14px] text-text">
                {typeof v === "number" ? fmtNum(v) : String(v)}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {sample.length > 0 ? (
        <div>
          <div className="text-[10.5px] uppercase tracking-[0.06em] text-muted-2 font-semibold mb-1.5">
            Sample events
          </div>
          <div className="border border-border rounded-md overflow-hidden">
            <table className="w-full text-[11.5px]">
              <thead className="bg-surface-2 text-muted-2">
                <tr>
                  <th className="text-left px-2 py-1 font-medium">Path</th>
                  <th className="text-left px-2 py-1 font-medium">Decision</th>
                  <th className="text-left px-2 py-1 font-medium">Device</th>
                  <th className="text-left px-2 py-1 font-medium">Codec</th>
                </tr>
              </thead>
              <tbody>
                {sample.slice(0, 8).map((row, idx) => (
                  <tr key={idx} className="border-t border-border">
                    <td className="px-2 py-1 font-mono truncate max-w-[280px]">
                      {String(row.source_path ?? "—")}
                    </td>
                    <td className="px-2 py-1">{String(row.decision ?? "—")}</td>
                    <td className="px-2 py-1">
                      {String(row.device_name ?? row.device_kind ?? "—")}
                    </td>
                    <td className="px-2 py-1 font-mono">{String(row.source_codec ?? "—")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {/* Fallback: anything we didn't render in the cells above */}
      {Object.keys(evidence).filter((k) => !["sample", ...Object.keys(counters)].includes(k))
        .length > 0 ? (
        <details className="text-[11.5px]">
          <summary className="cursor-pointer text-muted-2 hover:text-text">
            Raw evidence JSON
          </summary>
          <pre className="mt-1.5 p-2 border border-border rounded-md bg-surface-sunk font-mono overflow-x-auto">
            {JSON.stringify(evidence, null, 2)}
          </pre>
        </details>
      ) : null}
    </div>
  );
}

function pickRecord(src: Record<string, unknown>, keys: string[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of keys) {
    if (k in src && src[k] !== null && src[k] !== undefined) {
      out[k] = src[k];
    }
  }
  return out;
}

function fmtRuntime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const mins = Math.round(seconds / 60);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  const rem = mins % 60;
  return rem === 0 ? `${hours}h` : `${hours}h ${rem}m`;
}
