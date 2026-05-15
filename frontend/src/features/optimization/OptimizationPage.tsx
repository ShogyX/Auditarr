/**
 * Stage 5 — Optimization page (slim orchestrator).
 *
 * Composes:
 *   - ``PageHeader``                — title / sub / Run-next / New profile
 *   - ``OptimizationProfilesCard``  — profile list
 *   - ``OptimizationQueueCard``     — recent queue items
 *   - ``OptimizationProfileDialog`` — create / edit (Stage-1 Modal)
 *
 * Pre-Stage-5:  527 LOC
 * Post-Stage-5:  ~80 LOC (this file)
 *
 * Stage 5 also adopts the Stage-1 ``Modal`` primitive inside the
 * profile dialog. This is the first feature where Stage-1 primitive
 * adoption is *safe* (no tests pinning the previous DOM); the
 * Files and Rules features deferred adoption to "b" stages because
 * their existing tests pin specific contract that would invalidate.
 */

import { useMemo, useState } from "react";

import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { useHelpKey } from "@/hooks/useHelpKey";
import {
  useDeleteProfile,
  useOptimizationProfiles,
  useOptimizationQueueDetail,
  useRunNextOptimization,
  useUpdateProfile,
  type OptimizationProfile,
} from "@/hooks/useOptimization";

import { PlaybackStatsCard } from "@/features/playback/PlaybackStatsCard";

import { OptimizationProfileDialog } from "./OptimizationProfileDialog";
import { OptimizationProfilesCard } from "./OptimizationProfilesCard";
import { OptimizationQueueCard } from "./OptimizationQueueCard";

export function OptimizationPage() {
  useHelpKey("optimization.overview");

  const profiles = useOptimizationProfiles();
  const queue = useOptimizationQueueDetail({ limit: 50 });
  const runNext = useRunNextOptimization();
  const remove = useDeleteProfile();
  const update = useUpdateProfile();
  const [editing, setEditing] = useState<OptimizationProfile | "new" | null>(
    null,
  );

  const activeCount = useMemo(
    () =>
      queue.data?.filter(
        (i) => i.status === "queued" || i.status === "running",
      ).length ?? 0,
    [queue.data],
  );

  return (
    <>
      <PageHeader
        title="Optimization"
        sub="Transcoding profiles, the queue, and recent runs"
        helpKey="optimization.overview"
        actions={
          <>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => runNext.mutate()}
              disabled={runNext.isPending || activeCount === 0}
              title="Run the next queued item synchronously"
            >
              <Icon name="play" size={12} />
              <span className="ml-1">
                {runNext.isPending ? "Running…" : "Run next"}
              </span>
            </Button>
            <Button
              size="sm"
              variant="primary"
              onClick={() => setEditing("new")}
            >
              <Icon name="plus" size={12} />
              <span className="ml-1">New profile</span>
            </Button>
          </>
        }
      />
      <div className="p-6 flex flex-col gap-6 max-w-7xl">
        <OptimizationProfilesCard
          profiles={profiles}
          update={update}
          remove={remove}
          onEdit={setEditing}
        />
        {/* Stage 20 (audit follow-up): same PlaybackStatsCard the
            dashboard uses. Operators planning what to optimize next
            need transcode-rate + device-matrix context HERE, not
            two pages away. The card silently no-ops when the
            playback dataset is empty so it stays out of the way
            during the cold-start window. */}
        <PlaybackStatsCard />
        <OptimizationQueueCard queue={queue} pageSize={50} />
      </div>

      {editing ? (
        <OptimizationProfileDialog
          profile={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </>
  );
}
