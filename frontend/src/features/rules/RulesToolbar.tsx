/**
 * Stage 4 — Rules toolbar (Custom tab only).
 *
 * Extracted from the inline ``rules-toolbar`` JSX in ``RulesPage.tsx``.
 * Renders the search box + Import/Export buttons.
 *
 * The Import button toggles the parent's import-dialog state. The
 * Export button fires the bundle download (``apiClient`` call lives
 * here so the orchestrator doesn't need to import ``apiClient`` for a
 * single one-off — ADR-005's "no dynamic import" still holds; the
 * call is statically wired).
 */

import { Button } from "@/components/ui/Button";
import { Icon } from "@/components/ui/Icon";
import { toast } from "@/lib/toast";
import { apiClient } from "@/services/apiClient";

import { downloadJson } from "./rulesShared";

export interface RulesToolbarProps {
  search: string;
  onSearch: (s: string) => void;
  onImport: () => void;
  /** Total rule count — gates the Export button when zero. */
  ruleCount: number;
}

export function RulesToolbar({
  search,
  onSearch,
  onImport,
  ruleCount,
}: RulesToolbarProps) {
  async function onExport() {
    try {
      // ADR-005: ``apiClient`` is a singleton module statically
      // imported at the top of this file. A one-off fetch alongside
      // the useQuery ``useExportRules`` is intentional — we want a
      // direct download trigger, not React Query state.
      const bundle = await apiClient.get<{
        version: string;
        exported_at: string;
        rules: unknown[];
      }>("/rules/bundle/export");
      downloadJson(
        bundle,
        `auditarr-rules-${new Date().toISOString().slice(0, 10)}.json`,
      );
      toast(
        `Exported ${bundle.rules.length} rule${
          bundle.rules.length === 1 ? "" : "s"
        }`,
        "ok",
      );
    } catch (err) {
      toast(
        `Export failed: ${err instanceof Error ? err.message : String(err)}`,
        "error",
        5000,
      );
    }
  }

  return (
    <>
      <div className="rules-toolbar-search">
        <Icon
          name="search"
          size={14}
          className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted pointer-events-none"
        />
        <input
          type="search"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="Search rules…"
          className="settings-input pl-7"
          style={{ width: "100%" }}
        />
      </div>
      <div className="flex-1" />
      <Button size="sm" onClick={onImport}>
        <Icon name="upload" size={12} /> Import
      </Button>
      <Button
        size="sm"
        onClick={onExport}
        disabled={ruleCount === 0}
        title={
          ruleCount === 0
            ? "Nothing to export"
            : "Download all rules as a JSON bundle"
        }
      >
        <Icon name="download" size={12} /> Export
      </Button>
    </>
  );
}
