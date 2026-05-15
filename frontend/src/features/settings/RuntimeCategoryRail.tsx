/**
 * Stage 2 — Runtime settings category rail (left navigation).
 *
 * Extracted from the inline rail in RuntimeSettingsPanel. Renders
 * each category as a clickable rail item with a warning dot when the
 * category has unsaved changes in any of its fields.
 *
 * ``aria-current="page"`` on the active item matches the convention
 * used elsewhere in the shell (sidebar, breadcrumbs).
 */

import { Icon } from "@/components/ui/Icon";
import type { RuntimeCategory, RuntimeField } from "@/hooks/useRuntimeSettings";
import { cn } from "@/lib/cn";

export interface RuntimeCategoryRailProps {
  categories: RuntimeCategory[];
  activeKey: string | null;
  onSelect: (key: string) => void;
  fields: RuntimeField[];
  dirtyKeys: string[];
}

export function RuntimeCategoryRail({
  categories,
  activeKey,
  onSelect,
  fields,
  dirtyKeys,
}: RuntimeCategoryRailProps) {
  return (
    <nav className="runtime-rail" aria-label="Settings categories">
      {categories.map((c) => {
        const dirtyInCat = dirtyKeys.some(
          (k) => fields.find((f) => f.key === k)?.category === c.key,
        );
        const active = activeKey === c.key;
        return (
          <button
            key={c.key}
            type="button"
            onClick={() => onSelect(c.key)}
            className={cn("runtime-rail-item", active && "is-active")}
            aria-current={active ? "page" : undefined}
          >
            <span className="flex-1 text-left">{c.label}</span>
            {dirtyInCat ? (
              <span
                className="dot sev-warn"
                title="Pending changes in this category"
              />
            ) : null}
            <Icon name="chev_right" size={12} className="opacity-50" />
          </button>
        );
      })}
    </nav>
  );
}
