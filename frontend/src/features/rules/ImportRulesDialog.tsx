/**
 * Import rules dialog (Stage 24).
 *
 * Two-mode input: paste JSON, or upload a file. Conflict strategy
 * is a segmented control (skip / rename / overwrite). On submit,
 * the response's per-rule outcomes render as a list so the operator
 * sees exactly what happened — created / skipped / renamed /
 * overwritten / error — rather than just an aggregate count.
 *
 * Validation is layered: client-side checks the JSON parses; the
 * server validates each rule's definition and reports per-rule
 * errors without failing the whole batch.
 */

import { useRef, useState, type ChangeEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Pill } from "@/components/ui/Pill";
import {
  useImportRules,
  type ImportConflictStrategy,
  type RuleExportBundle,
  type RuleImportOutcome,
  type RuleImportResponse,
} from "@/hooks/useRules";
import { cn } from "@/lib/cn";

const STRATEGY_LABELS: Record<ImportConflictStrategy, string> = {
  skip: "Skip",
  rename: "Rename",
  overwrite: "Overwrite",
};

const STRATEGY_DESCRIPTIONS: Record<ImportConflictStrategy, string> = {
  skip: "Leave existing rules unchanged; imported duplicates are skipped.",
  rename: "Create imported duplicates alongside the existing rule with a unique suffix.",
  overwrite:
    "Replace the existing rule's definition with the imported one. Existing rule ID and evaluation history are preserved.",
};

export function ImportRulesDialog({ onClose }: { onClose: () => void }) {
  const importRules = useImportRules();
  const [text, setText] = useState<string>("");
  const [strategy, setStrategy] = useState<ImportConflictStrategy>("rename");
  const [parseError, setParseError] = useState<string | null>(null);
  const [result, setResult] = useState<RuleImportResponse | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  function onPickFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    file
      .text()
      .then((content) => {
        setText(content);
        setParseError(null);
        setResult(null);
      })
      .catch((err: unknown) => {
        setParseError(
          `Could not read file: ${err instanceof Error ? err.message : String(err)}`,
        );
      });
    // Reset the input so the same file can be picked again.
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function onSubmit() {
    setParseError(null);
    setResult(null);
    let bundle: RuleExportBundle;
    try {
      bundle = JSON.parse(text);
    } catch (err) {
      setParseError(
        `Invalid JSON: ${err instanceof Error ? err.message : String(err)}`,
      );
      return;
    }
    // Light client-side shape check. The server does the real
    // validation; this just catches "you pasted the wrong thing".
    if (!bundle || !Array.isArray(bundle.rules)) {
      setParseError(
        "JSON doesn't look like an Auditarr rules bundle (missing 'rules' array).",
      );
      return;
    }
    try {
      const response = await importRules.mutateAsync({
        bundle,
        onConflict: strategy,
      });
      setResult(response);
    } catch (err) {
      setParseError(
        err instanceof Error ? err.message : String(err),
      );
    }
  }

  const submitDisabled =
    importRules.isPending || text.trim().length === 0;

  return (
    <div
      className="dialog-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="import-dialog-title"
    >
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <div className="dialog-head">
          <h3 id="import-dialog-title" className="text-[14px] font-semibold m-0">
            Import rules
          </h3>
          <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close">
            <Icon name="x" size={12} />
          </Button>
        </div>

        <div className="dialog-body">
          {result ? (
            <ImportResultView outcomes={result.outcomes} response={result} />
          ) : (
            <>
              <p className="text-[12.5px] text-muted m-0">
                Paste a JSON bundle exported from another Auditarr
                instance, or upload a file. Conflicts are resolved per
                the strategy below.
              </p>

              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder='{"version":"1","rules":[…]}'
                rows={10}
                spellCheck={false}
                className={cn(
                  "px-2 py-2 text-[12px] font-mono bg-surface-sunk border rounded-md",
                  "focus:outline-none focus:ring-2 focus:ring-accent resize-y",
                  parseError ? "border-sev-error" : "border-border",
                )}
              />

              <div className="flex items-center gap-2">
                <Button size="sm" onClick={() => fileInputRef.current?.click()}>
                  <Icon name="upload" size={12} /> Upload file
                </Button>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="application/json,.json"
                  onChange={onPickFile}
                  className="hidden"
                />
                <span className="text-[11.5px] text-muted">
                  {text.length > 0
                    ? `${text.length.toLocaleString()} characters loaded`
                    : "no bundle loaded"}
                </span>
              </div>

              <div className="flex flex-col gap-2">
                <span className="text-[10.5px] uppercase tracking-[0.06em] text-muted-2 font-semibold">
                  On conflict
                </span>
                <div className="segmented" role="radiogroup" aria-label="Conflict strategy">
                  {(
                    Object.keys(STRATEGY_LABELS) as ImportConflictStrategy[]
                  ).map((s) => (
                    <button
                      key={s}
                      type="button"
                      role="radio"
                      aria-checked={strategy === s}
                      className={strategy === s ? "on" : ""}
                      onClick={() => setStrategy(s)}
                    >
                      {STRATEGY_LABELS[s]}
                    </button>
                  ))}
                </div>
                <span className="text-[11.5px] text-muted">
                  {STRATEGY_DESCRIPTIONS[strategy]}
                </span>
              </div>

              {parseError ? (
                <div className="runtime-warn">
                  <Icon name="alert" size={14} className="text-sev-warn shrink-0 mt-0.5" />
                  <span>{parseError}</span>
                </div>
              ) : null}
            </>
          )}
        </div>

        <div className="dialog-foot">
          {result ? (
            <Button size="sm" variant="accent" onClick={onClose}>
              Done
            </Button>
          ) : (
            <>
              <Button size="sm" onClick={onClose}>
                Cancel
              </Button>
              <Button
                size="sm"
                variant="accent"
                onClick={onSubmit}
                disabled={submitDisabled}
              >
                {importRules.isPending ? "Importing…" : "Import rules"}
              </Button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Result view ──────────────────────────────────────────────
function ImportResultView({
  outcomes,
  response,
}: {
  outcomes: RuleImportOutcome[];
  response: RuleImportResponse;
}) {
  return (
    <>
      <div className="flex items-center gap-3 flex-wrap">
        {response.created > 0 ? (
          <CountChip label="created" count={response.created} sev="ok" />
        ) : null}
        {response.renamed > 0 ? (
          <CountChip label="renamed" count={response.renamed} sev="info" />
        ) : null}
        {response.overwritten > 0 ? (
          <CountChip label="overwritten" count={response.overwritten} sev="info" />
        ) : null}
        {response.skipped > 0 ? (
          <CountChip label="skipped" count={response.skipped} />
        ) : null}
        {response.errors > 0 ? (
          <CountChip label="errors" count={response.errors} sev="error" />
        ) : null}
      </div>

      <div className="border border-border rounded-md overflow-hidden">
        <div className="px-3 py-2 text-[10.5px] uppercase tracking-[0.06em] font-semibold text-muted-2 bg-surface-2 border-b border-border">
          Per-rule outcomes
        </div>
        <ul className="m-0 p-0 list-none max-h-[260px] overflow-y-auto">
          {outcomes.map((o, i) => (
            <li
              key={`${o.name}-${i}`}
              className="flex items-center gap-2 px-3 py-2 border-t border-border first:border-t-0"
            >
              <Pill sev={severityForAction(o.action)}>{o.action}</Pill>
              <div className="min-w-0 flex-1">
                <div className="text-[12.5px] truncate">{o.final_name}</div>
                {o.name !== o.final_name ? (
                  <div className="text-[11px] text-muted-2 truncate">
                    was {o.name}
                  </div>
                ) : null}
                {o.error ? (
                  <div className="text-[11px] text-sev-error truncate">
                    {o.error}
                  </div>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}

function CountChip({
  label,
  count,
  sev,
}: {
  label: string;
  count: number;
  sev?: string;
}) {
  return (
    <span className="flex items-center gap-1.5">
      <Pill sev={sev}>{count.toLocaleString()}</Pill>
      <span className="text-[11.5px] text-muted">{label}</span>
    </span>
  );
}

function severityForAction(action: RuleImportOutcome["action"]): string | undefined {
  switch (action) {
    case "created":
      return "ok";
    case "renamed":
      return "info";
    case "overwritten":
      return "info";
    case "skipped":
      return undefined;
    case "error":
      return "error";
  }
}
