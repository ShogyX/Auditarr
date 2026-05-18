/**
 * v1.9 Stage 7.1 — StringChipEditor.
 *
 * Generic chip-list editor for string arrays. Replaces the
 * Stage 11 textarea-per-line for:
 *
 *   * ``source_whitelist`` (IPs / CIDRs / hostnames)
 *   * ``tag_allowlist`` / ``tag_denylist`` (case-insensitive
 *     tag names, Stage 7.2)
 *
 * Behavior:
 *   * Input + "Add" button (or Enter key) appends a trimmed,
 *     non-empty entry.
 *   * Each existing entry renders as a small chip with an "×"
 *     to remove.
 *   * Optional ``onAutoDiscover`` button calls the backend
 *     probe and renders a "Suggested entries" pill list the
 *     operator can click to add individually. We don't bulk-
 *     apply discovered IPs because some may be from
 *     misconfigured / hostile sources the operator doesn't
 *     want auto-trusted.
 *   * ``onAutoSuggestTags`` is a separate hook for the tag
 *     allow/deny case where the suggestion list comes from
 *     ``GET /integrations/{id}/upstream-tags`` (operator picks
 *     which to include).
 */

import { useState, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { Input } from "@/components/ui/Input";

export interface StringChipEditorProps {
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  /** Optional auto-discover. Returns a list of suggested entries
   *  the operator can pick from. */
  onAutoDiscover?: () => Promise<string[]>;
  /** Label for the discover button — e.g. "From recent
   *  deliveries" (webhooks) or "From upstream tags" (sonarr). */
  discoverLabel?: string;
  /** Optional label for clarity in test queries / a11y. */
  ariaLabel?: string;
}

export function StringChipEditor({
  value,
  onChange,
  placeholder = "Add an entry…",
  onAutoDiscover,
  discoverLabel = "Auto-discover",
  ariaLabel,
}: StringChipEditorProps) {
  const items = Array.isArray(value) ? value : [];
  const [draft, setDraft] = useState("");
  const [discovering, setDiscovering] = useState(false);
  const [suggestions, setSuggestions] = useState<string[] | null>(null);
  const [discoverError, setDiscoverError] = useState<string | null>(null);

  function commit() {
    const trimmed = draft.trim();
    if (!trimmed) return;
    // Dedupe (case-insensitive) — adding a chip that's already
    // in the list is a no-op rather than a duplicate entry.
    const lower = trimmed.toLowerCase();
    if (items.some((i) => i.toLowerCase() === lower)) {
      setDraft("");
      return;
    }
    onChange([...items, trimmed]);
    setDraft("");
  }

  function remove(idx: number) {
    onChange(items.filter((_, i) => i !== idx));
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      commit();
    }
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

  function pickSuggestion(s: string) {
    const lower = s.toLowerCase();
    if (items.some((i) => i.toLowerCase() === lower)) return;
    onChange([...items, s]);
  }

  return (
    <div
      className="flex flex-col gap-1.5"
      data-testid="string-chip-editor"
    >
      {/* Chips for existing entries. */}
      {items.length > 0 ? (
        <ul
          className="flex flex-wrap gap-1 p-0 m-0 list-none"
          aria-label={ariaLabel ? `${ariaLabel} chips` : "chips"}
        >
          {items.map((item, idx) => (
            <li key={`${item}-${idx}`}>
              <span className="inline-flex items-center gap-1 rounded border border-border bg-surface-2 px-1.5 py-0.5 text-[11.5px] font-mono">
                {item}
                <button
                  type="button"
                  onClick={() => remove(idx)}
                  className="text-muted-2 hover:text-text"
                  aria-label={`remove ${item}`}
                >
                  ×
                </button>
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      {/* Input + add button. */}
      <div className="flex items-center gap-1.5">
        <Input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
          placeholder={placeholder}
          aria-label={ariaLabel ?? "chip input"}
          className="flex-1 font-mono text-[12.5px]"
        />
        <Button
          size="sm"
          variant="ghost"
          onClick={commit}
          disabled={!draft.trim()}
        >
          <Icon name="plus" size={12} />
          <span className="ml-1">Add</span>
        </Button>
        {onAutoDiscover ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={discover}
            disabled={discovering}
            title={discoverLabel}
          >
            {discovering ? "Discovering…" : discoverLabel}
          </Button>
        ) : null}
      </div>

      {discoverError ? (
        <div className="text-[11.5px] text-sev-error">{discoverError}</div>
      ) : null}

      {suggestions ? (
        <SuggestionList
          suggestions={suggestions}
          existing={items}
          onPick={pickSuggestion}
          onDismiss={() => setSuggestions(null)}
        />
      ) : null}
    </div>
  );
}

function SuggestionList({
  suggestions,
  existing,
  onPick,
  onDismiss,
}: {
  suggestions: string[];
  existing: string[];
  onPick: (s: string) => void;
  onDismiss: () => void;
}) {
  const existingLower = new Set(existing.map((i) => i.toLowerCase()));
  const fresh = suggestions.filter(
    (s) => !existingLower.has(s.toLowerCase()),
  );
  if (fresh.length === 0) {
    return (
      <div className="text-[11.5px] text-muted-2">
        No new suggestions.{" "}
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
      className="rounded border border-border bg-surface-2 px-2 py-1.5"
      data-testid="string-chip-suggestions"
    >
      <div className="text-[11.5px] font-semibold mb-1">
        Suggestions ({fresh.length})
      </div>
      <div className="flex flex-wrap gap-1">
        {fresh.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onPick(s)}
            className="rounded border border-border bg-surface px-1.5 py-0.5 text-[11.5px] font-mono hover:bg-accent/15"
          >
            + {s}
          </button>
        ))}
      </div>
      <div className="pt-1.5">
        <Button size="sm" variant="ghost" onClick={onDismiss}>
          Dismiss
        </Button>
      </div>
    </div>
  );
}
