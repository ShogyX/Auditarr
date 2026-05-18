/**
 * Stage 14 (audit follow-up) — System maintenance affordances.
 *
 * Today this is just the docs reload button — when more
 * admin-only "do this thing now" maintenance buttons accrue, they
 * land in the same card so operators have one place to look.
 *
 * v1.9 Stage 2.6 — adds the factory-reset affordance at the bottom
 * inside a collapsed ``<details>`` block so it doesn't sit next to
 * the docs-reload button (a low-risk routine action) where an
 * operator might fat-finger it. The two-step UX:
 *
 *   <details>     →  operator expands the section
 *   button       →  operator clicks "Factory reset…"
 *   modal        →  operator types the confirm phrase
 *   button       →  operator clicks "Factory reset" (destructive)
 *
 * On success: toast → navigate to /. The current admin session is
 * preserved (the users table isn't truncated) so the operator
 * lands on a clean dashboard rather than a login screen.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { Card, CardBody } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { useFactoryReset, useReloadDocs } from "@/hooks/useSystem";
import { toast } from "@/lib/toast";
import { useAuthStore } from "@/stores/authStore";

import { FactoryResetDialog } from "./FactoryResetDialog";

export function SystemMaintenanceCard() {
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";

  const reloadDocs = useReloadDocs();
  // v1.9 Stage 2.6 — factory reset wiring.
  const factoryReset = useFactoryReset();
  const [resetOpen, setResetOpen] = useState(false);
  const navigate = useNavigate();

  if (!isAdmin) return null;

  function runFactoryReset(phrase: string) {
    factoryReset.mutate(phrase, {
      onSuccess: (result) => {
        toast(
          `Reset complete — ${result.tables_truncated} tables wiped${
            result.trash_purged ? ", trash directory cleared" : ""
          }.`,
          "ok",
          7000,
        );
        setResetOpen(false);
        // Land the operator back on a fresh dashboard. We don't
        // log them out because their user row was preserved on
        // purpose, but the cache is empty so a fresh page load
        // mirrors what a brand-new install looks like.
        navigate("/");
      },
      onError: (err) => {
        toast(
          `Factory reset failed: ${(err as Error).message}`,
          "error",
          5000,
        );
      },
    });
  }

  return (
    <Card>
      <CardBody>
        <div className="flex flex-col gap-3">
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

          {/* v1.9 Stage 2.6 — factory reset is deliberately hidden
              behind a <details> block at the bottom of the card so
              an operator clicking the routine "Reload docs" button
              can't accidentally land on the destructive one. The
              dialog adds a second gate (typed-phrase confirmation)
              before the API call fires. */}
          <details className="border-t border-border pt-3">
            <summary className="cursor-pointer text-[12px] text-muted-2 hover:text-text inline-flex items-center gap-1.5">
              <Icon name="alert" size={11} />
              <span>Danger zone</span>
            </summary>
            <div className="mt-3 flex items-center justify-between gap-3 flex-wrap">
              <div className="flex flex-col gap-0.5 min-w-0">
                <div className="text-[13px] font-semibold text-sev-error">
                  Factory reset
                </div>
                <div className="text-[11.5px] text-muted-2 max-w-xl">
                  Wipe every library, file, rule, integration, and
                  queued job. Your admin account is preserved so you
                  can rebuild from a clean slate.
                </div>
              </div>
              <Button
                size="sm"
                variant="danger"
                onClick={() => setResetOpen(true)}
                disabled={factoryReset.isPending}
                title="Open the factory-reset confirmation dialog"
              >
                <Icon name="trash" size={12} />
                <span className="ml-1">Factory reset…</span>
              </Button>
            </div>
          </details>
        </div>
      </CardBody>

      <FactoryResetDialog
        open={resetOpen}
        onOpenChange={setResetOpen}
        onConfirm={runFactoryReset}
        isPending={factoryReset.isPending}
      />
    </Card>
  );
}
