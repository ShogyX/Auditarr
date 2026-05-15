/**
 * Stage 5 — Optimization profiles card.
 *
 * Extracted from the inline Profiles section of ``OptimizationPage``.
 * Renders the card chrome + the four-way loading / error / empty /
 * data branch. Row rendering is delegated to ``OptimizationProfileRow``.
 *
 * State and mutation handlers stay in the parent (via props) so this
 * card has no React Query coupling — keeps it easy to drop into a
 * future combined operations dashboard or run inside Storybook.
 */

import { Card, CardBodyFlush, CardHead } from "@/components/ui/Card";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/ui/States";
import type {
  useDeleteProfile,
  useOptimizationProfiles,
  useUpdateProfile,
  OptimizationProfile,
} from "@/hooks/useOptimization";

import { OptimizationProfileRow } from "./OptimizationProfileRow";

export interface OptimizationProfilesCardProps {
  profiles: ReturnType<typeof useOptimizationProfiles>;
  update: ReturnType<typeof useUpdateProfile>;
  remove: ReturnType<typeof useDeleteProfile>;
  onEdit: (profile: OptimizationProfile) => void;
}

export function OptimizationProfilesCard({
  profiles,
  update,
  remove,
  onEdit,
}: OptimizationProfilesCardProps) {
  return (
    <Card>
      <CardHead
        title="Profiles"
        subtitle={
          profiles.data ? `${profiles.data.length} configured` : undefined
        }
      />
      <CardBodyFlush>
        {profiles.isLoading ? (
          <div className="px-4 py-6">
            <LoadingState label="Loading…" />
          </div>
        ) : profiles.isError ? (
          <div className="px-4 py-6">
            <ErrorState
              title="Failed to load profiles"
              description={(profiles.error as Error)?.message}
            />
          </div>
        ) : !profiles.data || profiles.data.length === 0 ? (
          <div className="px-4 py-6">
            <EmptyState
              icon="optimize"
              title="No profiles yet"
              description="Create a profile to start running optimizations from rules."
            />
          </div>
        ) : (
          profiles.data.map((p) => (
            <OptimizationProfileRow
              key={p.id}
              profile={p}
              onEdit={() => onEdit(p)}
              onToggle={() =>
                update.mutate({
                  id: p.id,
                  patch: { enabled: !p.enabled },
                })
              }
              onDelete={() => {
                if (confirm(`Delete profile "${p.name}"?`)) {
                  remove.mutate(p.id);
                }
              }}
            />
          ))
        )}
      </CardBodyFlush>
    </Card>
  );
}
