/**
 * Codec + container multi-select filter menu (Stage 31).
 *
 * Two grouped checkbox lists — codecs on top, containers on the
 * bottom — inside a single ``.popover``. The trigger button
 * shows an active count (codecs selected + containers selected)
 * as a mono badge, same visual language as the Columns menu's
 * "3/8" indicator. Click-outside and Escape close the popover.
 *
 * Vocabulary comes from the dashboard ``/dashboard/categories``
 * endpoint (Stage 26) so only codecs/containers that ACTUALLY
 * appear in the library show up as options. This avoids the
 * usual multi-select trap where the list grows to dozens of
 * never-encountered codecs from ffprobe's vocabulary and the
 * operator has to scroll past mpeg1video / sorenson / etc. to
 * find h264.
 *
 * The selection lives in the parent (FilesPage), passed in as
 * the canonical sets ``selectedCodecs`` and ``selectedContainers``.
 * The menu emits one event per checkbox change; the parent owns
 * the comma-joined query-string form.
 *
 * Stage 3 audit fix (Issue 9, codec/container half):
 *   - Defensive ``Array.isArray`` guard before iterating
 *     ``categories.data``. If the backend predates Stage 26 or
 *     ever regresses to a non-array success body, the popover
 *     degrades to a clear "no options yet" state instead of
 *     throwing ``TypeError: items is not iterable`` from the
 *     render path.
 *   - Empty-state copy now hints at the recovery path ("run a
 *     scan to populate") rather than reading as a flat dead end.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import {
  useDashboardCategories,
  type CategoryBreakdown,
} from "@/hooks/useDashboard";

interface CodecFilterMenuProps {
  selectedCodecs: Set<string>;
  selectedContainers: Set<string>;
  onToggleCodec: (codec: string) => void;
  onToggleContainer: (container: string) => void;
  onClear: () => void;
}

export function CodecFilterMenu({
  selectedCodecs,
  selectedContainers,
  onToggleCodec,
  onToggleContainer,
  onClear,
}: CodecFilterMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  // Fetch up to 64 distinct keys total (split roughly half-and-
  // half between codecs and containers in practice). 64 is well
  // above what any real library exhibits — most have 3-6 of
  // each — but small enough that we never paint a long list
  // that needs internal scrolling.
  const categories = useDashboardCategories(64);

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

  // Partition + sort by file count desc. The dashboard endpoint
  // already returns sorted-by-count, but defensive sort here
  // means the menu degrades gracefully if the contract changes.
  //
  // Stage 3 fix: ``Array.isArray`` guard. ``categories.data ?? []``
  // only protects against null/undefined; if the endpoint ever
  // returns a non-array success body (older backend, error shape
  // that React Query still treats as success, etc.) the
  // subsequent ``for...of`` would throw ``not iterable`` and
  // crash the render. Guarding here keeps the popover usable.
  const { codecs, containers } = useMemo(() => {
    const items: CategoryBreakdown[] = Array.isArray(categories.data)
      ? categories.data
      : [];
    const c: CategoryBreakdown[] = [];
    const k: CategoryBreakdown[] = [];
    for (const it of items) {
      if (it.group === "video_codec") c.push(it);
      else if (it.group === "container") k.push(it);
    }
    c.sort((a, b) => b.file_count - a.file_count);
    k.sort((a, b) => b.file_count - a.file_count);
    return { codecs: c, containers: k };
  }, [categories.data]);

  const activeCount = selectedCodecs.size + selectedContainers.size;

  return (
    <div ref={rootRef} className="relative">
      <Button
        size="sm"
        onClick={() => setOpen((v) => !v)}
        aria-label="Codec and container filters"
      >
        <Icon name="filter" size={12} />
        <span className="ml-1">Codec / container</span>
        {activeCount > 0 ? (
          <span className="font-mono text-muted-2 ml-1">
            {activeCount}
          </span>
        ) : null}
      </Button>
      {open ? (
        <div
          className="popover"
          role="menu"
          aria-label="Filter by codec or container"
        >
          {categories.isLoading ? (
            <div className="popover-head">Loading…</div>
          ) : categories.isError ? (
            <div className="popover-head text-sev-error">
              Couldn't load filter options
            </div>
          ) : (
            <>
              <div className="popover-head">
                Video codec{" "}
                <span className="font-mono text-muted-2 ml-1">
                  {codecs.length}
                </span>
              </div>
              {codecs.length === 0 ? (
                <div className="px-3 py-2 text-[12px] text-muted-2">
                  No probed codecs yet — run a scan to populate.
                </div>
              ) : (
                <ul className="m-0 p-0 list-none">
                  {codecs.map((c) => {
                    const checked = selectedCodecs.has(c.key);
                    return (
                      <li key={`codec:${c.key}`}>
                        <label className="popover-row">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => onToggleCodec(c.key)}
                          />
                          <span>{c.label}</span>
                          <span className="text-[10.5px] text-muted-2 ml-auto font-mono">
                            {c.file_count}
                          </span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              )}

              <div className="popover-head" style={{ marginTop: 4 }}>
                Container{" "}
                <span className="font-mono text-muted-2 ml-1">
                  {containers.length}
                </span>
              </div>
              {containers.length === 0 ? (
                <div className="px-3 py-2 text-[12px] text-muted-2">
                  No probed containers yet — run a scan to populate.
                </div>
              ) : (
                <ul className="m-0 p-0 list-none">
                  {containers.map((c) => {
                    const checked = selectedContainers.has(c.key);
                    return (
                      <li key={`container:${c.key}`}>
                        <label className="popover-row">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => onToggleContainer(c.key)}
                          />
                          <span>{c.label}</span>
                          <span className="text-[10.5px] text-muted-2 ml-auto font-mono">
                            {c.file_count}
                          </span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              )}
            </>
          )}
          <div className="popover-foot">
            <Button
              size="sm"
              variant="ghost"
              onClick={onClear}
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
