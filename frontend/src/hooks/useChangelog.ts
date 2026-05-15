/**
 * Stage 12 audit fix (Issue 17) — Changelog fetch hook.
 *
 * Attempts to load CHANGELOG.md content from a backend endpoint at
 * ``GET /api/v1/system/changelog``. That endpoint does not yet
 * exist server-side at the time this stage ships — the audit
 * recommended adding it but Stage 12 was scoped to frontend-only
 * files. The hook is intentionally tolerant of the missing endpoint:
 *   - On 404, the page renders a friendly empty state pointing
 *     operators at the file location.
 *   - On 200, the response is rendered as HTML via DocBody (same
 *     contract as ``/docs/{id}``).
 *
 * When the backend endpoint lands, no frontend changes are required:
 * this hook will start returning real data automatically.
 */

import { useQuery } from "@tanstack/react-query";

import { ApiError, apiClient } from "@/services/apiClient";

export interface ChangelogResponse {
  /** Pre-rendered HTML of CHANGELOG.md. */
  body_html: string;
  /** Optional raw markdown source for clients that want to render
   *  themselves. May be omitted to save bandwidth. */
  body_markdown?: string;
  /** ISO timestamp of the file's last modification, when known. */
  last_modified?: string | null;
}

export function useChangelog() {
  return useQuery<ChangelogResponse, ApiError>({
    queryKey: ["system", "changelog"],
    queryFn: () => apiClient.get<ChangelogResponse>("/system/changelog"),
    // The changelog is content the operator looks at occasionally,
    // not real-time data — cache aggressively. A new release won't
    // change the served file under a running server.
    staleTime: 30 * 60_000,
    // Don't retry on 404 — that's the "endpoint isn't implemented"
    // case and retrying just delays the empty state.
    retry: (failureCount, error) => {
      if (error instanceof ApiError && error.status === 404) return false;
      return failureCount < 2;
    },
  });
}
