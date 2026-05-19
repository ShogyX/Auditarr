import { Link } from "react-router-dom";

import { DocBody } from "@/components/ui/DocBody";
import { Icon } from "@/components/ui/Icon";
import { Pill } from "@/components/ui/Pill";
import { EmptyState, LoadingState } from "@/components/ui/States";
import { useDocPage, useHelpContext } from "@/hooks/useDocs";
import { cn } from "@/lib/cn";
import { useHelpStore } from "@/stores/helpStore";

/**
 * Slide-in help drawer. Closed by default; opened by the page header help
 * button or the global keyboard shortcut. Pulls its content from the
 * documentation engine using the active page's ``help_context`` key.
 */
export function HelpDrawer() {
  const isOpen = useHelpStore((s) => s.isOpen);
  const activeKey = useHelpStore((s) => s.activeKey);
  const close = useHelpStore((s) => s.close);

  // Stage 11 (audit follow-up): drop the ``isOpen ?`` gating on
  // these queries. Pre-Stage-11 the help-context lookup was paused
  // when the drawer was closed; reopening it for the SAME context
  // re-mounted the query and a brief loading state flashed before
  // React Query served the cached body. With the gating removed,
  // the query stays mounted and reopens are instant. The cost is
  // one extra background fetch per context the user has ever
  // hovered — small price for the better UX.
  const matches = useHelpContext(activeKey);
  const firstPageId = matches.data?.[0]?.id ?? null;
  const page = useDocPage(firstPageId);

  return (
    <>
      <div
        aria-hidden={!isOpen}
        onClick={close}
        className={cn(
          "fixed inset-0 z-40 bg-black/30 transition-opacity",
          isOpen ? "opacity-100" : "opacity-0 pointer-events-none",
        )}
      />
      <aside
        aria-hidden={!isOpen}
        aria-label="Contextual help"
        className={cn(
          // Stage 11 (audit follow-up): widen the drawer. Pre-Stage-11
          // it was clamped to ``max-w-md`` (28rem) which made every
          // doc with a code block or table feel cramped — operators
          // reported scrolling horizontally inside the drawer.
          // The new clamp scales by viewport: 28rem on small screens
          // (mobile), 36rem on md (laptops), 42rem on lg+ (desktop).
          "fixed top-0 right-0 z-50 h-screen w-full",
          "max-w-md md:max-w-xl lg:max-w-2xl",
          "bg-surface border-l border-border shadow-lg",
          "transform transition-transform duration-200 ease-out",
          "flex flex-col",
          isOpen ? "translate-x-0" : "translate-x-full",
        )}
      >
        <div className="flex items-center justify-between gap-3 px-4 h-header border-b border-border shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <Icon name="help" size={14} />
            <span className="text-[13px] font-semibold tracking-tight truncate">
              {page.data?.title ?? "Help"}
            </span>
            {activeKey ? <Pill className="ml-2 font-mono">{activeKey}</Pill> : null}
          </div>
          <button
            type="button"
            onClick={close}
            aria-label="Close help"
            className={cn(
              "h-7 w-7 rounded-[5px] inline-flex items-center justify-center",
              "border border-border bg-surface-2 text-text-2 hover:bg-[var(--hover)] transition-colors",
            )}
          >
            <Icon name="x" size={14} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-6">
          <HelpBody activeKey={activeKey} firstPageId={firstPageId} />
        </div>

        <div className="px-4 py-3 border-t border-border shrink-0">
          <Link
            to="/help"
            onClick={close}
            className="text-[12.5px] text-muted hover:text-text-2 inline-flex items-center gap-1.5"
          >
            Browse all documentation
            <Icon name="arrow_up_right" size={12} />
          </Link>
        </div>
      </aside>
    </>
  );
}

function HelpBody({
  activeKey,
  firstPageId,
}: {
  activeKey: string | null;
  firstPageId: string | null;
}) {
  const matches = useHelpContext(activeKey);
  const page = useDocPage(firstPageId);

  if (!activeKey) {
    return (
      <EmptyState
        icon="info"
        title="No help context yet"
        description="This screen doesn't declare a help context. Help drawers populate themselves when a screen sets `useHelpKey('…')`."
      />
    );
  }

  // Stage 11 (audit follow-up): only show the loading state on
  // the FIRST fetch — never during a background refetch when we
  // already have cached data. ``isPending`` is true only when the
  // query has no data yet; ``isFetching`` is true on background
  // refresh and we explicitly ignore it. This is what eliminates
  // the "open the drawer → flicker → settled" loop.
  if ((matches.isPending && !matches.data) || (page.isPending && !page.data)) {
    return <LoadingState label="Loading help…" />;
  }

  if (!matches.data || matches.data.length === 0) {
    return (
      <EmptyState
        icon="info"
        title="No documentation found"
        description={`No documentation page declares help_context: ${activeKey}.`}
      />
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {matches.data.length > 1 ? (
        <div className="text-[11.5px] uppercase tracking-wide text-muted font-semibold">
          {matches.data.length} related pages
        </div>
      ) : null}

      {page.data ? <DocBody html={page.data.body_html} /> : null}

      {matches.data.length > 1 ? (
        <div className="border-t border-border pt-4 mt-2 flex flex-col gap-2">
          <div className="text-[11.5px] uppercase tracking-wide text-muted font-semibold">
            Other relevant pages
          </div>
          {matches.data
            .filter((m) => m.id !== firstPageId)
            .map((m) => (
              <Link
                key={m.id}
                to={`/help#${m.id}`}
                className="text-[13px] text-accent hover:underline"
              >
                {m.title}
              </Link>
            ))}
        </div>
      ) : null}
    </div>
  );
}
