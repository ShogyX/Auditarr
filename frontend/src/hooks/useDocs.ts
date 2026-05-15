import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/services/apiClient";

export interface DocSummary {
  id: string;
  title: string;
  category: string;
  tags: string[];
  summary: string;
  help_contexts: string[];
}

export interface DocPage extends DocSummary {
  body_html: string;
  body_markdown: string;
  related: string[];
  source_path: string;
  last_modified: string | null;
}

export interface DocSearchHit {
  page_id: string;
  title: string;
  category: string;
  score: number;
  excerpt: string;
}

export function useDocList(filters?: { category?: string; tag?: string }) {
  return useQuery({
    queryKey: ["docs", "list", filters ?? {}],
    queryFn: () => {
      const params = new URLSearchParams();
      if (filters?.category) params.set("category", filters.category);
      if (filters?.tag) params.set("tag", filters.tag);
      const qs = params.toString();
      return apiClient.get<DocSummary[]>(`/docs${qs ? `?${qs}` : ""}`);
    },
    staleTime: 5 * 60_000,
  });
}

export function useDocCategories() {
  return useQuery({
    queryKey: ["docs", "categories"],
    queryFn: () => apiClient.get<Record<string, DocSummary[]>>("/docs/categories"),
    staleTime: 5 * 60_000,
  });
}

export function useDocPage(pageId: string | null | undefined) {
  return useQuery({
    queryKey: ["docs", "page", pageId],
    enabled: !!pageId,
    queryFn: () => apiClient.get<DocPage>(`/docs/${pageId}`),
    staleTime: 5 * 60_000,
  });
}

export function useDocSearch(query: string) {
  const trimmed = query.trim();
  return useQuery({
    queryKey: ["docs", "search", trimmed],
    enabled: trimmed.length > 0,
    queryFn: () =>
      apiClient.get<DocSearchHit[]>(`/docs/search?q=${encodeURIComponent(trimmed)}&limit=20`),
    staleTime: 60_000,
  });
}

export function useHelpContext(key: string | null | undefined) {
  return useQuery({
    queryKey: ["docs", "help", key],
    enabled: !!key,
    queryFn: () => apiClient.get<DocSummary[]>(`/docs/help/${key}`),
    staleTime: 5 * 60_000,
  });
}
