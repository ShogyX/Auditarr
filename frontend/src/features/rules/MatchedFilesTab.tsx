/**
 * Stage 14b (audit follow-up) — Matched files tab.
 *
 * Renders the per-rule list of files this rule has matched, joined
 * server-side to ``media_files`` for path / filename / severity.
 * Rows are ordered by severity_rank desc then evaluated_at desc
 * (highest-impact first).
 *
 * Click-through cross-links to the Files page via
 * ``/files?file_id=...``. The Files page's state hook honors that
 * URL param and opens the detail drawer for the named file on
 * mount.
 *
 * Empty / loading / error states render the matching ``States``
 * helpers for visual consistency with the rest of the app.
 */

import { Link } from "react-router-dom";

import { Pill } from "@/components/ui/Pill";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/ui/States";
import { useRuleMatchedFiles } from "@/hooks/useRules";

function severityKind(sev: string): "ok" | "warn" | "error" | undefined {
  switch (sev) {
    case "ok":
    case "info":
      return "ok";
    case "warn":
    case "high":
      return "warn";
    case "error":
    case "crit":
      return "error";
    default:
      return undefined;
  }
}

export interface MatchedFilesTabProps {
  ruleId: string;
}

export function MatchedFilesTab({ ruleId }: MatchedFilesTabProps) {
  const query = useRuleMatchedFiles(ruleId);

  if (query.isLoading) {
    return (
      <div className="py-6">
        <LoadingState label="Loading matched files…" />
      </div>
    );
  }
  if (query.isError) {
    return (
      <div className="py-6">
        <ErrorState
          title="Failed to load matched files"
          description={(query.error as Error)?.message}
        />
      </div>
    );
  }
  if (!query.data || query.data.length === 0) {
    return (
      <div className="py-6">
        <EmptyState
          icon="files"
          title="No matches"
          description="This rule has not matched any files yet. Run a re-evaluation to populate matches."
        />
      </div>
    );
  }

  return (
    <div
      className="files-table-wrap"
      data-testid="rule-matched-files-table"
    >
      <table className="files-table" role="grid">
        <thead>
          <tr>
            <th>File</th>
            <th>Severity</th>
            <th>Evaluated</th>
          </tr>
        </thead>
        <tbody>
          {query.data.map((row) => (
            <tr key={row.media_file_id} className="files-table-row">
              <td>
                <Link
                  to={`/files?file_id=${encodeURIComponent(row.media_file_id)}`}
                  className="text-[12.5px] hover:underline"
                  title={row.path}
                >
                  {row.filename}
                </Link>
                <div className="text-[10.5px] font-mono text-muted-2 truncate">
                  {row.path}
                </div>
              </td>
              <td>
                <Pill sev={severityKind(row.severity)}>{row.severity}</Pill>
              </td>
              <td className="text-[11.5px] text-muted-2 font-mono">
                {new Date(row.evaluated_at).toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
