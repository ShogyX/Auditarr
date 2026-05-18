/**
 * v1.9 Stage 4.4 — Templates tab content.
 *
 * Lists every shipped rule template (reference-quality bodies)
 * with a "Use template" button per row. Clicking inserts a
 * normal operator-owned ``Rule`` row and navigates to its editor,
 * pre-populated with the template body so the operator can tune
 * it before saving.
 *
 * Design choices:
 *   * Sorted by priority asc (same as the Stage 4.5 evaluation-
 *     order panel — operators learn one mental model).
 *   * Inline description; templates are reference material, the
 *     description IS the documentation.
 *   * "Use template" is a button, not a row click — accidental
 *     row clicks while reading shouldn't materialize a Rule.
 *   * Error UX: a 4xx/5xx surfaces as inline error text on the
 *     row. We DON'T navigate on error.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui/States";
import { apiClient } from "@/services/apiClient";
import {
  useRuleTemplates,
  useUseRuleTemplate,
  type RuleTemplate,
} from "@/hooks/useRules";

export function RuleTemplatesTab() {
  const templatesQuery = useRuleTemplates();

  if (templatesQuery.isLoading) {
    return (
      <div className="p-4">
        <LoadingState label="Loading templates…" />
      </div>
    );
  }
  if (templatesQuery.isError) {
    return (
      <div className="p-4">
        <ErrorState
          title="Couldn't load rule templates"
          description={(templatesQuery.error as Error)?.message}
        />
      </div>
    );
  }
  const templates = templatesQuery.data ?? [];
  if (templates.length === 0) {
    return (
      <div className="p-4 space-y-3">
        <EmptyState
          icon="info"
          title="No templates available"
          description="Templates are seeded on app startup. If this list is unexpectedly empty after upgrading to v1.9, the seed pass may not have run — try the Re-seed button below."
        />
        <ReseedButton />
      </div>
    );
  }

  return (
    <div>
      <div className="px-4 py-2 flex items-center justify-between border-b border-border">
        <span className="text-[11.5px] text-muted-2">
          {templates.length} built-in template{templates.length === 1 ? "" : "s"}
        </span>
        <ReseedButton />
      </div>
      <ul className="list-none m-0 p-0">
        {templates.map((t) => (
          <TemplateRow key={t.id} template={t} />
        ))}
      </ul>
    </div>
  );
}

function ReseedButton() {
  const [status, setStatus] = useState<
    | { kind: "idle" }
    | { kind: "running" }
    | {
        kind: "done";
        inserted: number;
        refreshed: number;
        total: number;
      }
    | { kind: "error"; message: string }
  >({ kind: "idle" });

  async function onReseed() {
    setStatus({ kind: "running" });
    try {
      const result = await apiClient.post<{
        inserted: number;
        refreshed: number;
        unchanged: number;
        total_after: number;
      }>("/rule-templates/reseed");
      setStatus({
        kind: "done",
        inserted: result.inserted,
        refreshed: result.refreshed,
        total: result.total_after,
      });
      // Re-trigger the templates query so the UI updates.
      window.location.reload();
    } catch (err) {
      setStatus({
        kind: "error",
        message:
          err instanceof Error ? err.message : "Re-seed failed",
      });
    }
  }

  return (
    <div className="flex items-center gap-2">
      <Button
        size="sm"
        variant="ghost"
        onClick={onReseed}
        disabled={status.kind === "running"}
        title="Re-run the built-in templates seed (admin)"
        data-testid="reseed-templates-button"
      >
        <Icon name="refresh" size={12} />
        <span className="ml-1">
          {status.kind === "running" ? "Re-seeding…" : "Re-seed"}
        </span>
      </Button>
      {status.kind === "done" ? (
        <span className="text-[11px] text-muted-2">
          {status.inserted} inserted, {status.refreshed} refreshed,{" "}
          {status.total} total.
        </span>
      ) : null}
      {status.kind === "error" ? (
        <span className="text-[11px] text-sev-error">{status.message}</span>
      ) : null}
    </div>
  );
}

function TemplateRow({ template }: { template: RuleTemplate }) {
  const navigate = useNavigate();
  const useMutation = useUseRuleTemplate();
  const [error, setError] = useState<string | null>(null);

  async function onUse() {
    setError(null);
    try {
      const rule = await useMutation.mutateAsync(template.id);
      navigate(`/rules/${rule.id}/edit`);
    } catch (err) {
      setError((err as Error)?.message ?? "Failed to create rule");
    }
  }

  return (
    <li className="flex items-start gap-3 px-4 py-3 border-b border-border last:border-b-0">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[11px] text-muted-2 tabular-nums w-9 text-right shrink-0">
            {template.priority}
          </span>
          <span className="text-[13.5px] font-medium truncate">
            {template.name}
          </span>
        </div>
        {template.description ? (
          <div className="text-[12px] text-muted-2 mt-1 ml-11">
            {template.description}
          </div>
        ) : null}
        {error ? (
          <div className="text-[12px] text-sev-error mt-1 ml-11">{error}</div>
        ) : null}
      </div>
      <div className="shrink-0">
        <Button
          size="sm"
          variant="ghost"
          onClick={onUse}
          disabled={useMutation.isPending}
          title="Create a Rule from this template"
        >
          <Icon name="plus" size={12} />
          <span className="ml-1">
            {useMutation.isPending ? "Creating…" : "Use template"}
          </span>
        </Button>
      </div>
    </li>
  );
}
