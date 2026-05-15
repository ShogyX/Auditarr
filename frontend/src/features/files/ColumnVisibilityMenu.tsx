/**
 * Column visibility menu (Stage 23).
 *
 * Popover of checkboxes toggling table column visibility. Always-
 * required columns are rendered disabled+checked so the operator
 * can see they're locked in (matching the prototype's UX) rather
 * than just hidden from the menu.
 *
 * Click-outside is handled at the popover root; Escape closes too.
 * The popover trigger is exported as a discrete component so the
 * Files page can position it inline in the toolbar.
 */

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";

export interface ColumnVisibilityOption {
  key: string;
  label: string;
  always?: boolean;
}

interface ColumnVisibilityMenuProps {
  columns: readonly ColumnVisibilityOption[];
  visible: readonly string[];
  onToggle: (key: string) => void;
  onReset: () => void;
}

export function ColumnVisibilityMenu({
  columns,
  visible,
  onToggle,
  onReset,
}: ColumnVisibilityMenuProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

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

  const visibleSet = new Set(visible);

  return (
    <div ref={rootRef} className="relative">
      <Button size="sm" onClick={() => setOpen((v) => !v)}>
        <Icon name="columns" size={12} /> Columns{" "}
        <span className="font-mono text-muted-2 ml-1">
          {visible.length}/{columns.length}
        </span>
      </Button>
      {open ? (
        <div className="popover" role="menu" aria-label="Visible columns">
          <div className="popover-head">Visible columns</div>
          <ul className="m-0 p-0 list-none">
            {columns.map((c) => {
              const checked = visibleSet.has(c.key);
              const disabled = !!c.always;
              return (
                <li key={c.key}>
                  <label
                    className={cn(
                      "popover-row",
                      disabled && "is-disabled",
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={disabled}
                      onChange={() => !disabled && onToggle(c.key)}
                    />
                    <span>{c.label}</span>
                    {disabled ? (
                      <span className="text-[10.5px] text-muted-2 ml-auto">
                        required
                      </span>
                    ) : null}
                  </label>
                </li>
              );
            })}
          </ul>
          <div className="popover-foot">
            <Button size="sm" variant="ghost" onClick={onReset}>
              Reset
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
