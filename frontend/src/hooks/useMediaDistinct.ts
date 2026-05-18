/**
 * v1.9 Stage 3.1 — distinct-values hook.
 *
 * Drives the ColumnFilterPopover on every filterable column
 * header. The popover queries here on open + on each
 * search-input keystroke (debounced by the caller via
 * ``staleTime`` plus a debounced ``prefix`` arg from the
 * component).
 *
 * Backend whitelist of legal ``field`` values is enforced
 * server-side; passing something not in the whitelist returns
 * 422 (the hook surfaces this as a normal React Query error).
 */

import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/services/apiClient";

export interface DistinctValueRead {
  /** ``null`` = the bucket of files where this column IS NULL.
   *  The popover renders it as "(none)". */
  value: string | null;
  count: number;
}

export interface DistinctValuesResponse {
  field: string;
  values: DistinctValueRead[];
  truncated: boolean;
}

export interface UseMediaDistinctOptions {
  libraryId?: string | null;
  prefix?: string | null;
  /** Pass ``false`` to suspend the query — useful while the
   *  popover is closed so we don't keep hammering the endpoint. */
  enabled?: boolean;
}

export function useMediaDistinct(
  field: string,
  { libraryId = null, prefix = null, enabled = true }: UseMediaDistinctOptions = {},
) {
  return useQuery({
    queryKey: ["media", "distinct", field, libraryId, prefix],
    queryFn: () => {
      const params = new URLSearchParams({ field });
      if (libraryId) params.set("library_id", libraryId);
      if (prefix && prefix.trim()) params.set("prefix", prefix.trim());
      return apiClient.get<DistinctValuesResponse>(
        `/media/distinct?${params.toString()}`,
      );
    },
    enabled,
    // 60s staleTime so re-opening the popover within a minute
    // skips the network round-trip. Counts can drift if the
    // scanner is running concurrently, but a stale-by-a-minute
    // count is still strictly better than a blocking spinner
    // every time the operator clicks the filter icon.
    staleTime: 60_000,
  });
}
