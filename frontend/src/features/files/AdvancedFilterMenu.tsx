/**
 * v1.10 — Files-page "Advanced" filter popover.
 *
 * Hosts four axes that the toolbar didn't cover:
 *
 *   - Tag include / exclude (with an AND/OR toggle for the include set)
 *   - Rule include / exclude (also with AND/OR for include)
 *   - Subtitles tri-state
 *   - Resolution bucket select
 *
 * Vocabulary comes from ``/api/v1/tags`` and ``/api/v1/rules``.
 * Selections live on the parent's page state; this menu emits one
 * event per toggle and reads the current sets to drive checkbox
 * state. Stays focused on the chip-style includes/excludes UX —
 * the existing CodecFilterMenu handles codec/container; this
 * companion handles the rest.
 */

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { useRules } from "@/hooks/useRules";
import type { ResolutionBucket } from "@/hooks/useMedia";
import { apiClient } from "@/services/apiClient";

function useTagCatalog() {
  // v1.10 — minimal tag-catalog fetch local to the menu. A
  // shared ``useTagNames`` lands with the tag-management PR;
  // this scope-local hook keeps the file self-contained until
  // that PR merges.
  return useQuery({
    queryKey: ["tags", "names"],
    queryFn: () => apiClient.get<string[]>("/tags"),
    staleTime: 60_000,
  });
}

const RESOLUTION_OPTIONS: { value: ResolutionBucket | ""; label: string }[] = [
  { value: "", label: "Any" },
  { value: "sd", label: "SD (< 480p)" },
  { value: "480p", label: "480p" },
  { value: "720p", label: "720p" },
  { value: "1080p", label: "1080p" },
  { value: "1440p", label: "1440p" },
  { value: "2160p", label: "2160p / 4K" },
  { value: "8k", label: "4320p / 8K" },
  { value: "unknown", label: "Unknown" },
];

interface AdvancedFilterMenuProps {
  tagsInclude: Set<string>;
  tagsExclude: Set<string>;
  tagsIncludeAll: boolean;
  onToggleTagInclude: (tag: string) => void;
  onToggleTagExclude: (tag: string) => void;
  onTagsIncludeAll: (v: boolean) => void;
  rulesInclude: Set<string>;
  rulesExclude: Set<string>;
  rulesIncludeAll: boolean;
  onToggleRuleInclude: (ruleId: string) => void;
  onToggleRuleExclude: (ruleId: string) => void;
  onRulesIncludeAll: (v: boolean) => void;
  hasSubtitles: boolean | undefined;
  onHasSubtitles: (v: boolean | undefined) => void;
  resolutionBucket: ResolutionBucket | "";
  onResolutionBucket: (v: ResolutionBucket | "") => void;
  onClearAll: () => void;
}

export function AdvancedFilterMenu(props: AdvancedFilterMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const tagNames = useTagCatalog();
  const rules = useRules();

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const activeCount =
    props.tagsInclude.size +
    props.tagsExclude.size +
    props.rulesInclude.size +
    props.rulesExclude.size +
    (props.hasSubtitles !== undefined ? 1 : 0) +
    (props.resolutionBucket ? 1 : 0);

  return (
    <div ref={rootRef} className="relative">
      <Button
        size="sm"
        onClick={() => setOpen((v) => !v)}
        aria-label="Advanced filters: tags, rules, subtitles, resolution"
      >
        <Icon name="filter" size={12} />
        <span className="ml-1">Advanced</span>
        {activeCount > 0 ? (
          <span className="font-mono text-muted-2 ml-1">{activeCount}</span>
        ) : null}
      </Button>
      {open ? (
        <div
          className="popover"
          role="menu"
          aria-label="Advanced filters"
          style={{ minWidth: 340, maxHeight: 480, overflowY: "auto" }}
        >
          {/* ── Tags ─────────────────────────────────────── */}
          <div className="popover-head" style={{ display: "flex", alignItems: "center" }}>
            <span>
              Tags{" "}
              <span className="font-mono text-muted-2 ml-1">
                {tagNames.data?.length ?? 0}
              </span>
            </span>
            <label
              className="ml-auto text-[11px] text-muted-2 inline-flex items-center gap-1"
              title="When on, files must carry every selected tag (AND). Off = any of the selected tags (OR)."
            >
              <input
                type="checkbox"
                checked={props.tagsIncludeAll}
                onChange={(e) => props.onTagsIncludeAll(e.target.checked)}
              />
              all-of
            </label>
          </div>
          <ChipList<string>
            label="Include"
            items={tagNames.data ?? []}
            selected={props.tagsInclude}
            onToggle={props.onToggleTagInclude}
            isLoading={tagNames.isLoading}
            valueKey={(t) => t}
            labelOf={(t) => t}
          />
          <ChipList<string>
            label="Exclude"
            items={tagNames.data ?? []}
            selected={props.tagsExclude}
            onToggle={props.onToggleTagExclude}
            isLoading={tagNames.isLoading}
            valueKey={(t) => t}
            labelOf={(t) => t}
            variant="exclude"
          />

          {/* ── Rules ────────────────────────────────────── */}
          <div
            className="popover-head"
            style={{ marginTop: 6, display: "flex", alignItems: "center" }}
          >
            <span>
              Rules{" "}
              <span className="font-mono text-muted-2 ml-1">
                {rules.data?.length ?? 0}
              </span>
            </span>
            <label
              className="ml-auto text-[11px] text-muted-2 inline-flex items-center gap-1"
              title="When on, files must match every selected rule (AND). Off = any of the selected rules (OR)."
            >
              <input
                type="checkbox"
                checked={props.rulesIncludeAll}
                onChange={(e) => props.onRulesIncludeAll(e.target.checked)}
              />
              all-of
            </label>
          </div>
          <ChipList<{ id: string; name: string }>
            label="Include"
            items={rules.data ?? []}
            selected={props.rulesInclude}
            onToggle={props.onToggleRuleInclude}
            isLoading={rules.isLoading}
            valueKey={(r) => r.id}
            labelOf={(r) => r.name}
          />
          <ChipList<{ id: string; name: string }>
            label="Exclude"
            items={rules.data ?? []}
            selected={props.rulesExclude}
            onToggle={props.onToggleRuleExclude}
            isLoading={rules.isLoading}
            valueKey={(r) => r.id}
            labelOf={(r) => r.name}
            variant="exclude"
          />

          {/* ── Subtitles + Resolution ───────────────────── */}
          <div className="popover-head" style={{ marginTop: 6 }}>
            Subtitles
          </div>
          <div className="px-3 py-1.5 flex gap-2">
            {(
              [
                { v: undefined, label: "Any" },
                { v: true, label: "Has subs" },
                { v: false, label: "No subs" },
              ] as const
            ).map((opt) => (
              <button
                key={String(opt.v)}
                type="button"
                onClick={() => props.onHasSubtitles(opt.v)}
                className={
                  props.hasSubtitles === opt.v
                    ? "pill on"
                    : "pill"
                }
                aria-pressed={props.hasSubtitles === opt.v}
              >
                {opt.label}
              </button>
            ))}
          </div>

          <div className="popover-head">Resolution</div>
          <div className="px-3 py-1.5">
            <select
              className="settings-input"
              value={props.resolutionBucket}
              onChange={(e) =>
                props.onResolutionBucket(
                  e.target.value as ResolutionBucket | "",
                )
              }
              aria-label="Resolution bucket filter"
            >
              {RESOLUTION_OPTIONS.map((o) => (
                <option key={o.value || "any"} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>

          <div className="popover-foot">
            <Button
              size="sm"
              variant="ghost"
              onClick={props.onClearAll}
              disabled={activeCount === 0}
            >
              Clear all
            </Button>
            <Button size="sm" onClick={() => setOpen(false)}>
              Done
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

interface ChipListProps<T> {
  label: string;
  items: T[];
  selected: Set<string>;
  onToggle: (value: string) => void;
  isLoading: boolean;
  valueKey: (item: T) => string;
  labelOf: (item: T) => string;
  variant?: "include" | "exclude";
}

function ChipList<T>({
  label,
  items,
  selected,
  onToggle,
  isLoading,
  valueKey,
  labelOf,
  variant = "include",
}: ChipListProps<T>) {
  if (isLoading) {
    return (
      <div className="px-3 py-1 text-[12px] text-muted-2">{label}: loading…</div>
    );
  }
  if (items.length === 0) {
    return (
      <div className="px-3 py-1 text-[12px] text-muted-2">
        {label}: nothing to pick yet
      </div>
    );
  }
  return (
    <div className="px-3 py-1.5">
      <div className="text-[11px] text-muted-2 mb-1">{label}</div>
      <div className="flex flex-wrap gap-1">
        {items.map((it) => {
          const key = valueKey(it);
          const on = selected.has(key);
          const cls = on
            ? variant === "exclude"
              ? "pill on text-sev-error"
              : "pill on"
            : "pill";
          return (
            <button
              key={key}
              type="button"
              onClick={() => onToggle(key)}
              className={cls}
              aria-pressed={on}
              title={labelOf(it)}
              style={{ maxWidth: 200 }}
            >
              <span className="truncate inline-block max-w-full">
                {labelOf(it)}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
