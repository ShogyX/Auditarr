import type { ReactNode } from "react";

import { Icon } from "@/components/ui/Icon";
import { cn } from "@/lib/cn";
import { useHelpStore } from "@/stores/helpStore";

interface PageHeaderProps {
  title: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
  /** When set, renders a help button in the header that opens this key. */
  helpKey?: string;
  className?: string;
}

export function PageHeader({ title, sub, actions, helpKey, className }: PageHeaderProps) {
  const open = useHelpStore((s) => s.open);

  return (
    <header
      className={cn(
        "flex items-start justify-between gap-4 px-6 py-4 border-b border-border bg-surface",
        className,
      )}
    >
      <div className="min-w-0">
        <h1 className="text-[18px] font-semibold tracking-tight m-0">{title}</h1>
        {sub ? <div className="mt-0.5 text-[12.5px] text-muted">{sub}</div> : null}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {actions}
        {helpKey ? (
          <button
            type="button"
            onClick={() => open(helpKey)}
            aria-label="Help for this screen"
            title="Help (⌘/)"
            className={cn(
              "h-7 w-7 rounded-[5px] inline-flex items-center justify-center",
              "border border-border bg-surface-2 text-text-2 hover:bg-[var(--hover)] transition-colors",
            )}
          >
            <Icon name="help" size={14} />
          </button>
        ) : null}
      </div>
    </header>
  );
}
