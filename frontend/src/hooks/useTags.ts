import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { invalidateRelated } from "@/lib/invalidate";
import { apiClient } from "@/services/apiClient";

export interface TagSummaryRow {
  name: string;
  source: string;
  file_count: number;
}

export interface TagDeleteRequest {
  name?: string;
  source?: string;
}

export interface TagDeleteResponse {
  deleted: number;
}

export function useTagSummary() {
  return useQuery({
    queryKey: ["tags", "summary"],
    queryFn: () => apiClient.get<TagSummaryRow[]>("/tags/summary"),
    staleTime: 30_000,
  });
}

export function useTagNames() {
  // Re-exposed alongside the summary so consumers that only need
  // names (rule editor autocomplete) don't pay for the count query.
  return useQuery({
    queryKey: ["tags", "names"],
    queryFn: () => apiClient.get<string[]>("/tags"),
    staleTime: 60_000,
  });
}

export function useBulkDeleteTags() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: TagDeleteRequest) =>
      apiClient.post<TagDeleteResponse>("/tags/delete", body),
    onSuccess: () => {
      // Tags surface in: the catalog list (rule editor / automation
      // chip-input), the management table, and the Files page tag
      // column. Invalidate all of them.
      invalidateRelated(qc, "tags");
      invalidateRelated(qc, "media");
    },
  });
}
