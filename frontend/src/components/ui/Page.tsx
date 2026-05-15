/**
 * Stage 1 — Page scaffold.
 *
 * Replaces the ad-hoc ``<header> + <div>`` layout pattern in every feature
 * page with one component that owns:
 *   - the page header (title / sub / actions / helpKey)
 *   - the body padding (--page-pad-x / --page-pad-y-*)
 *   - the body max-width (--page-max-width, centred)
 *
 * Feature pages should compose ``<Page>`` and put their content in children.
 *
 * Design package source: ``.page-body { padding: 22px 28px 60px; max-width:
 * 1480px; margin: 0 auto; }`` + ``.page-header``.
 *
 * Usage:
 *
 *   <Page
 *     title="Files"
 *     sub="487 files across 3 libraries"
 *     helpKey="files.overview"
 *     actions={<Button>New scan</Button>}
 *   >
 *     {...page body...}
 *   </Page>
 *
 * For tabbed pages, render the ``Tabs`` primitive in ``actions`` (right of
 * title) or as the first body element — both are common in the design
 * package.
 */

import type { HTMLAttributes, ReactNode } from "react";

import { PageHeader } from "@/components/shell/PageHeader";
import { cn } from "@/lib/cn";

export interface PageProps extends Omit<HTMLAttributes<HTMLDivElement>, "title"> {
  title: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
  helpKey?: string;
  /** Set ``flush`` to remove body padding (full-bleed content). */
  flush?: boolean;
  /** Optional render slot below the header, above the body (e.g. tabs). */
  toolbar?: ReactNode;
}

export function Page({
  title,
  sub,
  actions,
  helpKey,
  flush = false,
  toolbar,
  className,
  children,
  ...rest
}: PageProps) {
  return (
    <div className={cn("flex flex-col min-h-full", className)} {...rest}>
      <PageHeader title={title} sub={sub} actions={actions} helpKey={helpKey} />
      {toolbar ? (
        <div className="border-b border-border bg-surface">
          <div className="mx-auto max-w-page px-page-x">{toolbar}</div>
        </div>
      ) : null}
      <main
        className={cn(
          "flex-1 mx-auto w-full max-w-page",
          !flush && "px-page-x pt-page-y-top pb-page-y-bottom",
        )}
      >
        {children}
      </main>
    </div>
  );
}
