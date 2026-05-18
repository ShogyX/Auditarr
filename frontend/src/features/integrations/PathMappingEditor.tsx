/**
 * v1.9 Stage 7.1 — PathMappingEditor.
 *
 * Replaces the Stage 11 textarea for the ``path_mappings`` config
 * field with a structured per-row editor. Each row is two side-
 * by-side inputs (from / to) plus a delete button. A "+ Add" row
 * appends a blank entry. "Auto-discover" calls the backend probe
 * endpoint and merges in suggested rows (operator can still edit
 * before saving).
 *
 * Design choices:
 *   * The data model stays an array of ``{from: string, to:
 *     string}`` objects — same shape the backend already accepts
 *     and ``parse_mappings`` already parses.
 *   * We DON'T validate paths client-side (no canonicalization,
 *     no trailing-slash normalization) — the backend's
 *     ``parse_mappings`` is the source of truth on what's
 *     acceptable. Client-side validation here would duplicate
 *     that logic and drift over time.
 *   * Auto-discover failures show inline; we don't fail the
 *     whole editor.
 *   * "Apply suggestion" merges by appending; we don't dedupe
 *     against existing rows because two rows with the same
 *     ``from`` are a configuration error the operator should
 *     see, not silently hidden.
 */

import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Input } from "@/components/ui/Input";
import { cn } from "@/lib/cn";

export interface PathMappingRow {
  from: string;
  to: string;
}

export interface PathMappingSuggestion {
  from: string;
  to: string;
  confidence: "high" | "medium" | "low" | "none";
  library_id: string | null;
  library_name: string | null;
}

export interface PathMappingEditorProps {
  value: PathMappingRow[];
  onChange: (next: PathMappingRow[]) => void;
  /** Optional auto-discover callback. When undefined, the
   *  "Auto-discover" button is hidden (for cases where the
   *  integration kind doesn't support discovery). */
  onAutoDiscover?: () => Promise<PathMappingSuggestion[]>;
}

export function PathMappingEditor({
  value,
  onChange,
  onAutoDiscover,
}: PathMappingEditorProps) {
  const rows = Array.isArray(value) ? value : [];
  const [discovering, setDiscovering] = useState(false);
  const [suggestions, setSuggestions] = useState<
    PathMappingSuggestion[] | null
  >(null);
  const [discoverError, setDiscoverError] = useState<string | null>(null);

  function update(idx: number, patch: Partial<PathMappingRow>) {
    const next = rows.map((r, i) => (i === idx ? { ...r, ...patch } : r));
    onChange(next);
  }

  function remove(idx: number) {
    onChange(rows.filter((_, i) => i !== idx));
  }

  function add() {
    onChange([...rows, { from: "", to: "" }]);
  }

  async function discover() {
    if (!onAutoDiscover) return;
    setDiscovering(true);
    setDiscoverError(null);
    try {
      const result = await onAutoDiscover();
      setSuggestions(result);
    } catch (err) {
      setDiscoverError((err as Error)?.message ?? "Discovery failed");
    } finally {
      setDiscovering(false);
    }
  }

  function applySuggestions() {
    if (!suggestions || suggestions.length === 0) return;
    // Append; don't dedupe. See module comment.
    onChange([
      ...rows,
      ...suggestions
        .filter((s) => s.from)
        .map((s) => ({ from: s.from, to: s.to })),
    ]);
    setSuggestions(null);
  }

  return (
    <div
      className="flex flex-col gap-1.5"
      data-testid="path-mapping-editor"
    >
      {rows.length === 0 ? (
        <div className="text-[11.5px] text-muted-2 italic">
          No mappings configured. Paths are used 1:1.
        </div>
      ) : (
        <ul className="list-none p-0 m-0 flex flex-col gap-1.5">
          {rows.map((row, idx) => (
            <li
              key={idx}
              className="flex items-center gap-1.5"
              data-testid={`path-mapping-row-${idx}`}
            >
              <Input
                type="text"
                value={row.from}
                onChange={(e) => update(idx, { from: e.target.value })}
                placeholder="From (integration view)"
                aria-label={`path mapping ${idx + 1} from`}
                className="flex-1 min-w-0 font-mono text-[12.5px]"
              />
              <span className="text-muted-2 select-none">→</span>
              <Input
                type="text"
                value={row.to}
                onChange={(e) => update(idx, { to: e.target.value })}
                placeholder="To (Auditarr view)"
                aria-label={`path mapping ${idx + 1} to`}
                className="flex-1 min-w-0 font-mono text-[12.5px]"
              />
              <Button
                size="sm"
                variant="ghost"
                onClick={() => remove(idx)}
                title="Remove this mapping"
                aria-label={`remove path mapping ${idx + 1}`}
              >
                <Icon name="trash" size={12} />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center gap-2 pt-1">
        <Button size="sm" variant="ghost" onClick={add}>
          <Icon name="plus" size={12} />
          <span className="ml-1">Add mapping</span>
        </Button>
        {onAutoDiscover ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={discover}
            disabled={discovering}
            title="Probe the upstream for its root folders and suggest mappings"
          >
            {discovering ? "Discovering…" : "Auto-discover"}
          </Button>
        ) : null}
      </div>

      {discoverError ? (
        <div className="text-[11.5px] text-sev-error">{discoverError}</div>
      ) : null}

      {suggestions ? (
        <SuggestionPanel
          suggestions={suggestions}
          onApply={applySuggestions}
          onDismiss={() => setSuggestions(null)}
        />
      ) : null}
    </div>
  );
}

function SuggestionPanel({
  suggestions,
  onApply,
  onDismiss,
}: {
  suggestions: PathMappingSuggestion[];
  onApply: () => void;
  onDismiss: () => void;
}) {
  if (suggestions.length === 0) {
    return (
      <div className="mt-1 rounded border border-border bg-surface-2 px-2 py-1.5 text-[11.5px] text-muted-2">
        No suggestions found. Configure mappings manually below.{" "}
        <button
          type="button"
          className="text-accent underline"
          onClick={onDismiss}
        >
          Dismiss
        </button>
      </div>
    );
  }
  return (
    <div
      className="mt-1 rounded border border-border bg-surface-2 px-2 py-1.5"
      data-testid="path-mapping-suggestions"
    >
      <div className="text-[11.5px] font-semibold mb-1">
        Suggested mappings ({suggestions.length})
      </div>
      <ul className="list-none p-0 m-0 flex flex-col gap-0.5 text-[11.5px] font-mono">
        {suggestions.map((s, i) => (
          <li key={i} className="flex items-center gap-1">
            <span className="text-muted-2 w-12 shrink-0">
              {s.confidence}
            </span>
            <span className="truncate">{s.from}</span>
            <span className="text-muted-2 mx-1">→</span>
            <span
              className={cn(
                "truncate",
                s.to ? "" : "italic text-muted-2",
              )}
            >
              {s.to || "(no library match)"}
            </span>
          </li>
        ))}
      </ul>
      <div className="flex items-center gap-2 pt-1.5">
        <Button size="sm" onClick={onApply}>
          Apply all
        </Button>
        <Button size="sm" variant="ghost" onClick={onDismiss}>
          Dismiss
        </Button>
      </div>
    </div>
  );
}
