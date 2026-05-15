/**
 * Stage 14 (audit follow-up) — System maintenance affordances.
 *
 * Today this is just the docs reload button — when more
 * admin-only "do this thing now" maintenance buttons accrue, they
 * land in the same card so operators have one place to look.
 *
 * The docs index is built at process boot from ``docs/*.md``. The
 * reload endpoint re-walks the directory without restarting the
 * server — useful when an operator drops a new page on disk via
 * a sidecar deploy. Toast surfaces the loaded page count on
 * success.
 */

import { Card, CardBody } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { useReloadDocs } from "@/hooks/useSystem";
import { toast } from "@/lib/toast";
import { useAuthStore } from "@/stores/authStore";

export function SystemMaintenanceCard() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";

  const reloadDocs = useReloadDocs();

  if (!isAdmin) return null;

  return (
    <Card>
      <CardBody>
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex flex-col gap-0.5 min-w-0">
            <div className="text-[13px] font-semibold">Maintenance</div>
            <div className="text-[11.5px] text-muted-2">
              Re-walk the documentation directory and rebuild the
              search index without restarting the server.
            </div>
          </div>
          <Button
            size="sm"
            variant="ghost"
            disabled={reloadDocs.isPending}
            onClick={() => {
              reloadDocs.mutate(undefined, {
                onSuccess: (result) => {
                  toast(`Reloaded ${result.count} pages`, "ok");
                },
                onError: (err) => {
                  toast(
                    `Docs reload failed: ${(err as Error).message}`,
                    "error",
                  );
                },
              });
            }}
            title="Reload the documentation index"
            aria-label="Reload documentation index"
          >
            <Icon name="refresh" size={12} />
            <span className="ml-1">
              {reloadDocs.isPending ? "Reloading…" : "Reload docs"}
            </span>
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}
