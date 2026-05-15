/**
 * Stage 12 audit fix (Issue 17) — Help page is now docs-only.
 *
 * The UpdaterPanel (installed/available version + apply history)
 * moved to the Changelog page so the Help page can stay focused on
 * documentation: search, category tree, page reader. No other
 * behavior changed — the docs nav, search, and page renderer are
 * unchanged.
 *
 * The previous page mixed two distinct mental models — "I need to
 * read about X" (docs) and "what version am I on" (updater) — in
 * one route. The audit flagged that as the root cause for Issue 17;
 * this split lets each surface stand on its own.
 */

import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { PageHeader } from "@/components/shell/PageHeader";
import { Card, CardBody, CardBodyFlush, CardHead } from "@/components/ui/Card";
import { DocBody } from "@/components/ui/DocBody";
import { Icon } from "@/components/ui/Icon";
import { Pill, Tag } from "@/components/ui/Pill";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import {
  useDocCategories,
  useDocPage,
  useDocSearch,
  type DocSearchHit,
  type DocSummary,
} from "@/hooks/useDocs";
import { useHelpKey } from "@/hooks/useHelpKey";
import { cn } from "@/lib/cn";

export function HelpPage() {
  useHelpKey("help.docs");

  const location = useLocation();
  const navigate = useNavigate();
  const categories = useDocCategories();

  const initialId = location.hash ? location.hash.slice(1) : null;
  const [selectedId, setSelectedId] = useState<string | null>(initialId);
  const [query, setQuery] = useState("");

  // Pick a default page once data arrives, if no hash was supplied.
  useEffect(() => {
    if (selectedId) return;
    const first = firstPage(categories.data);
    if (first) setSelectedId(first.id);
  }, [categories.data, selectedId]);

  // Update the URL hash so links to specific pages survive reloads/share.
  useEffect(() => {
    if (selectedId) {
      navigate(`#${selectedId}`, { replace: true });
    }
  }, [selectedId, navigate]);

  return (
    <>
      {/* Stage 12: title trimmed from "Help & updates" to "Help" —
          the "updates" half moved to the Changelog page. */}
      <PageHeader title="Help" sub="Documentation, search, and contextual references" />
      <div className="grid grid-cols-1 lg:grid-cols-[280px_minmax(0,1fr)] gap-6 p-6">
        <DocsNav
          categories={categories.data}
          isLoading={categories.isLoading}
          selectedId={selectedId}
          onSelect={setSelectedId}
          query={query}
          onQuery={setQuery}
        />
        <DocPageView pageId={selectedId} />
      </div>
    </>
  );
}

function firstPage(data: Record<string, DocSummary[]> | undefined): DocSummary | null {
  if (!data) return null;
  const cats = Object.keys(data).sort();
  for (const c of cats) {
    const list = data[c];
    if (list && list.length > 0) return list[0] ?? null;
  }
  return null;
}

function DocsNav({
  categories,
  isLoading,
  selectedId,
  onSelect,
  query,
  onQuery,
}: {
  categories: Record<string, DocSummary[]> | undefined;
  isLoading: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
  query: string;
  onQuery: (q: string) => void;
}) {
  const search = useDocSearch(query);

  return (
    <Card className="lg:sticky lg:top-6 self-start">
      <div className="px-3 pt-3 pb-2">
        <div className="relative">
          <Icon
            name="search"
            size={14}
            className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none"
          />
          <input
            type="search"
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            placeholder="Search docs…"
            className={cn(
              "w-full h-8 pl-8 pr-2 text-[13px]",
              "bg-surface-2 border border-border rounded-md",
              "focus:outline-none focus:border-border-strong focus:ring-2 focus:ring-accent",
              "placeholder:text-muted-2",
            )}
          />
        </div>
      </div>
      <CardBodyFlush>
        {query.trim().length > 0 ? (
          <SearchResults isLoading={search.isLoading} hits={search.data} onSelect={onSelect} />
        ) : (
          <CategoryTree
            categories={categories}
            isLoading={isLoading}
            selectedId={selectedId}
            onSelect={onSelect}
          />
        )}
      </CardBodyFlush>
    </Card>
  );
}

function CategoryTree({
  categories,
  isLoading,
  selectedId,
  onSelect,
}: {
  categories: Record<string, DocSummary[]> | undefined;
  isLoading: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const ordered = useMemo(
    () => (categories ? Object.keys(categories).sort((a, b) => a.localeCompare(b)) : []),
    [categories],
  );

  if (isLoading) return <LoadingState label="Loading docs…" />;
  if (!categories || ordered.length === 0) {
    return (
      <EmptyState
        icon="info"
        title="No documentation"
        description="Drop Markdown files into the docs/ directory and reload."
      />
    );
  }

  return (
    <div className="pb-2">
      {ordered.map((category) => {
        const pages = categories[category] ?? [];
        return (
          <div key={category} className="mt-1">
            <div className="px-3 py-1.5 text-[10.5px] uppercase tracking-[0.08em] text-muted-2 font-semibold">
              {category}
            </div>
            {pages
              .slice()
              .sort((a, b) => a.title.localeCompare(b.title))
              .map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => onSelect(p.id)}
                  className={cn(
                    "w-full text-left px-3 h-8 text-[12.5px] truncate flex items-center",
                    "hover:bg-[var(--hover)] transition-colors",
                    selectedId === p.id
                      ? "bg-[var(--active)] text-text font-medium"
                      : "text-text-2",
                  )}
                  title={p.title}
                >
                  {p.title}
                </button>
              ))}
          </div>
        );
      })}
    </div>
  );
}

function SearchResults({
  isLoading,
  hits,
  onSelect,
}: {
  isLoading: boolean;
  hits: DocSearchHit[] | undefined;
  onSelect: (id: string) => void;
}) {
  if (isLoading) return <LoadingState label="Searching…" />;
  if (!hits || hits.length === 0) {
    return (
      <EmptyState
        icon="search"
        title="No matches"
        description="Try a different query, or browse by category."
      />
    );
  }
  return (
    <div className="pb-2">
      {hits.map((hit) => (
        <button
          key={hit.page_id}
          type="button"
          onClick={() => onSelect(hit.page_id)}
          className={cn(
            "w-full text-left px-3 py-2.5 border-b border-border last:border-b-0",
            "hover:bg-[var(--hover)] transition-colors",
          )}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-[12.5px] font-medium truncate">{hit.title}</span>
            <Tag>{hit.category}</Tag>
          </div>
          {/* Stage 13 audit fix (Issue 21): excerpt rendering. The
              backend search index already weights body content
              (_BODY_WEIGHT = 1.0 in backend/app/documentation/search.py)
              and the DocSearchHit type carries the surrounding text
              in ``excerpt``. The excerpt was already rendered here
              prior to Stage 13 — Stage 13 only refines the styling
              to match the audit's exact spec (text-muted-2 / mt-0.5
              / 11px) so the excerpt reads as clearly subordinate
              "supporting text" relative to the title. The functional
              fix (content-aware search WITH visible excerpt) was
              already in place. */}
          <div className="text-[11px] text-muted-2 mt-0.5 line-clamp-2">
            {hit.excerpt}
          </div>
        </button>
      ))}
    </div>
  );
}

function DocPageView({ pageId }: { pageId: string | null }) {
  const page = useDocPage(pageId);

  if (!pageId) {
    return (
      <Card>
        <CardBody>
          <EmptyState
            icon="info"
            title="Select a page"
            description="Pick a topic from the left, or use search."
          />
        </CardBody>
      </Card>
    );
  }
  if (page.isLoading) {
    return (
      <Card>
        <CardBody>
          <LoadingState label="Loading page…" />
        </CardBody>
      </Card>
    );
  }
  if (page.isError || !page.data) {
    return (
      <Card>
        <CardBody>
          <ErrorState
            title="Page not found"
            description={(page.error as Error | undefined)?.message}
          />
        </CardBody>
      </Card>
    );
  }

  return (
    <Card>
      <CardHead
        title={
          <span className="inline-flex items-center gap-2">
            <Tag>{page.data.category}</Tag>
            <span className="truncate">{page.data.title}</span>
          </span>
        }
        actions={
          page.data.help_contexts[0] ? (
            <Pill className="font-mono">{page.data.help_contexts[0]}</Pill>
          ) : null
        }
      />
      <CardBody>
        <DocBody html={page.data.body_html} />
        {page.data.related.length > 0 ? (
          <div className="border-t border-border pt-4 mt-6 flex flex-wrap items-center gap-2">
            <span className="text-[11.5px] uppercase tracking-wide text-muted font-semibold">
              Related
            </span>
            {page.data.related.map((r) => (
              <Tag key={r}>{r}</Tag>
            ))}
          </div>
        ) : null}
      </CardBody>
    </Card>
  );
}
