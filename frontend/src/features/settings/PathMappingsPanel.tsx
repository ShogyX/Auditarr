/**
 * Path-mappings editor (Stage 22).
 *
 * Aggregates ``Integration.config.path_mappings`` across every
 * integration into one editable surface. Backend payload comes from
 * ``GET /api/v1/system/path-mappings`` (non-admin-visible) and writes
 * go through ``PUT /api/v1/system/path-mappings/{integration_id}``
 * (admin-only).
 *
 * Why this matters: integrations report file paths from their own
 * perspective (Plex thinks ``/data/media/movies``, Auditarr's scanner
 * sees ``/mnt/storage/Movies``). The scanner needs to translate
 * between the two, and the operator needs ONE place to audit the
 * translation rules — not 6 integration pages.
 *
 * The panel is integration-by-integration: an accordion of rows
 * showing each integration's current mappings, with inline editing
 * and per-integration save. We do NOT do a bulk save across
 * integrations because the backend PUT is per-integration — keeping
 * the UX aligned with the API contract prevents the "I changed three
 * things and one of them silently failed" failure mode.
 */

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card, CardHead } from "@/components/ui/Card";
import { Icon } from "@/components/ui/Icon";
import { Pill } from "@/components/ui/Pill";
import { EmptyState, LoadingState } from "@/components/ui/States";
import { cn } from "@/lib/cn";
import { toast } from "@/lib/toast";
import {
  ApiError,
  useCreateGlobalPathMapping,
  useDeleteGlobalPathMapping,
  useGlobalPathMappings,
  usePathMappings,
  usePathSuggestions,
  useRediscoverPaths,
  useUpdatePathMappings,
  type GlobalPathMappingRow,
  type PathMapping,
  type PathMappingsIntegration,
} from "@/hooks/useRuntimeSettings";
import { useLibraries } from "@/hooks/useMedia";
import { useAuthStore } from "@/stores/authStore";

export function PathMappingsPanel() {
  const q = usePathMappings();

  if (q.isLoading) {
    return (
      <Card>
        <CardHead title="Path mappings" subtitle="Loading…" />
        <div className="p-6">
          <LoadingState label="Loading path mappings…" />
        </div>
      </Card>
    );
  }
  if (q.isError) {
    // Non-admins still get the read; a hard error here is unexpected.
    return (
      <Card>
        <CardHead title="Path mappings" />
        <div className="p-6">
          <EmptyState
            icon="folder"
            title="Could not load path mappings"
            description="Refresh the page to retry."
          />
        </div>
      </Card>
    );
  }

  const integrations = q.data?.integrations ?? [];

  return (
    <Card>
      <CardHead
        title="Path mappings"
        subtitle="How each integration's paths translate to Auditarr's view"
      />
      <div className="p-4 flex flex-col gap-3">
        {/* Stage 14 audit fix (Issue 12): explainer text at the top of
            the panel for first-time operators. The CardHead subtitle
            is terse ("How each integration's paths translate to
            Auditarr's view") which assumes the reader already knows
            what path-mapping is. This sentence makes the value
            proposition explicit so an operator new to the surface
            isn't left guessing which side is which. */}
        <div className="text-[12px] text-muted-2 -mt-1 mb-1 leading-relaxed">
          Maps the integration&rsquo;s view of a file path (left) to
          Auditarr&rsquo;s local view of the same file (right). Only
          needed when the two see the same media under different
          paths — e.g. Plex sees{" "}
          <code className="font-mono">/data/media/movies</code> but
          your scanner sees{" "}
          <code className="font-mono">/mnt/storage/Movies</code>.
        </div>
        {/* Stage 5 (audit follow-up): global mappings sit above the
            per-integration list because they're applied first when
            resolving paths and operators usually want to see them
            in priority order. */}
        <GlobalPathMappingsSection />
        {integrations.length === 0 ? (
          <EmptyState
            icon="folder"
            title="No integrations configured"
            description="Add an integration first, then come back here to map its paths to Auditarr's view."
          />
        ) : (
          integrations.map((ig) => (
            <PathMappingsForIntegration
              key={ig.integration_id}
              integration={ig}
            />
          ))
        )}
      </div>
    </Card>
  );
}

interface RowDraft {
  from: string;
  to: string;
  // The original mapping this row came from (if any) — used to detect
  // dirtiness against the server state for the dirty/save indicator.
  // Empty string for newly added rows.
  origFrom: string;
  origTo: string;
}

function PathMappingsForIntegration({
  integration,
}: {
  integration: PathMappingsIntegration;
}) {
  const update = useUpdatePathMappings();

  // Build the draft from the server data. We rebuild on integration
  // changes so a successful save (which invalidates the query) brings
  // the draft back in sync with the truth.
  const [rows, setRows] = useState<RowDraft[]>(() =>
    toDrafts(integration.mappings),
  );
  useEffect(() => {
    setRows(toDrafts(integration.mappings));
  }, [integration.mappings]);

  const trimmed = rows
    .map((r) => ({ from: r.from.trim(), to: r.to.trim() }))
    .filter((r) => r.from || r.to);
  const incomplete = trimmed.some((r) => !r.from || !r.to);
  const dirty = !sameMappings(trimmed, integration.mappings);

  function addRow() {
    setRows((prev) => [...prev, { from: "", to: "", origFrom: "", origTo: "" }]);
  }

  function deleteRow(idx: number) {
    setRows((prev) => prev.filter((_, i) => i !== idx));
  }

  function patchRow(idx: number, patch: Partial<Pick<RowDraft, "from" | "to">>) {
    setRows((prev) =>
      prev.map((r, i) => (i === idx ? { ...r, ...patch } : r)),
    );
  }

  function reset() {
    setRows(toDrafts(integration.mappings));
  }

  async function save() {
    if (incomplete) {
      toast("Each mapping needs both a from and a to path.", "warn");
      return;
    }
    try {
      await update.mutateAsync({
        integrationId: integration.integration_id,
        mappings: trimmed.map((r) => ({ from: r.from, to: r.to })),
      });
      toast(`Saved path mappings for ${integration.name}`, "ok");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      toast(`Save failed: ${msg}`, "error", 5000);
    }
  }

  return (
    <div
      className={cn(
        "path-mapping-card",
        dirty && "is-dirty",
      )}
    >
      <div className="path-mapping-head">
        <span className="text-[13px] font-medium">{integration.name}</span>
        <Pill>{integration.kind}</Pill>
        {!integration.is_active ? <Pill>disabled</Pill> : null}
        <span className="flex-1" />
        {dirty ? <span className="text-[11.5px] text-muted">unsaved changes</span> : null}
      </div>
      {/* Stage 17 (audit follow-up): discovered-paths snapshot panel.
          Highlights upstream paths the operator hasn't mapped yet
          and existing mappings whose 'from' side no longer matches
          any discovered path (stale). Never auto-applies — operator
          drives every Add/Remove via the buttons. */}
      <DiscoverySection
        integration={integration}
        rows={rows}
        onAddSuggestion={(upstream_path) => {
          setRows((prev) => [
            ...prev,
            { from: upstream_path, to: "", origFrom: "", origTo: "" },
          ]);
        }}
      />
      {rows.length === 0 ? (
        <div className="text-[12.5px] text-muted italic py-2 px-1">
          {/* Stage 14 audit fix (Issue 12): empty-state copy now
              explicitly points at the always-visible Add-mapping
              button below the section, so a first-time operator
              doesn't have to scan for it. */}
          No mappings configured. Click <span className="font-medium not-italic">+ Add mapping</span> below
          if this integration&rsquo;s paths differ from Auditarr&rsquo;s.
        </div>
      ) : (
        <div className="path-mapping-rows">
          {/* Stage 14 audit fix (Issue 12): column-header tooltips so
              hovering reveals which side is which. The labels stay
              terse so the row layout doesn't grow; the explainer at
              the top of the panel carries the same information at
              page-load time. */}
          <div className="path-mapping-row path-mapping-row-head">
            <span title="The path as the integration reports it (e.g. what Plex sends in scrobble events).">
              From (integration)
            </span>
            <span title="The same file as Auditarr's scanner sees it on this host.">
              To (Auditarr)
            </span>
            <span />
          </div>
          {rows.map((r, i) => (
            <div key={i} className="path-mapping-row">
              <input
                className="settings-input mono"
                placeholder="/data/media/movies"
                value={r.from}
                onChange={(e) => patchRow(i, { from: e.target.value })}
              />
              <LocalPathInput
                value={r.to}
                onChange={(next) => patchRow(i, { to: next })}
              />
              <Button
                size="sm"
                variant="ghost"
                onClick={() => deleteRow(i)}
                title="Remove mapping"
                aria-label="Remove mapping"
              >
                <Icon name="trash" size={12} />
              </Button>
            </div>
          ))}
        </div>
      )}

      <div className="path-mapping-foot">
        <Button size="sm" onClick={addRow}>
          <Icon name="plus" size={12} /> Add mapping
        </Button>
        <span className="flex-1" />
        {dirty ? (
          <>
            <Button size="sm" onClick={reset} disabled={update.isPending}>
              Reset
            </Button>
            <Button
              size="sm"
              variant="accent"
              onClick={save}
              disabled={update.isPending || incomplete}
            >
              {update.isPending ? "Saving…" : "Save"}
            </Button>
          </>
        ) : null}
      </div>
    </div>
  );
}

function toDrafts(mappings: PathMapping[]): RowDraft[] {
  return mappings.map((m) => ({
    from: m.from,
    to: m.to,
    origFrom: m.from,
    origTo: m.to,
  }));
}

function sameMappings(
  a: { from: string; to: string }[],
  b: PathMapping[],
): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const ai = a[i]!;
    const bi = b[i]!;
    if (ai.from !== bi.from || ai.to !== bi.to) return false;
  }
  return true;
}


// ── Stage 5: Global path mappings section ──────────────────────
function GlobalPathMappingsSection() {
  const q = useGlobalPathMappings();
  const create = useCreateGlobalPathMapping();
  const remove = useDeleteGlobalPathMapping();
  const suggestions = usePathSuggestions();

  const [newFrom, setNewFrom] = useState("");
  const [newTo, setNewTo] = useState("");

  async function onAdd() {
    if (!newFrom.trim() || !newTo.trim()) {
      toast("Both from and to paths are required.", "warn");
      return;
    }
    try {
      await create.mutateAsync({
        from_path: newFrom.trim(),
        to_path: newTo.trim(),
      });
      setNewFrom("");
      setNewTo("");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      toast(`Add failed: ${msg}`, "error", 5000);
    }
  }

  async function onDelete(row: GlobalPathMappingRow) {
    if (
      !window.confirm(
        `Delete global mapping ${row.from_path} → ${row.to_path}?`,
      )
    ) {
      return;
    }
    try {
      await remove.mutateAsync(row.id);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      toast(`Delete failed: ${msg}`, "error", 5000);
    }
  }

  // Build the autocomplete option list from the suggestions
  // endpoint: every known root (library, integration mapping
  // endpoint, or existing global path) becomes a hint. Dedup
  // across the three sources.
  const suggestionList = [
    ...(suggestions.data?.library_roots ?? []),
    ...((suggestions.data?.integration_paths ?? []).flatMap((p) => [p.from, p.to])),
    ...(suggestions.data?.global_paths ?? []),
  ];
  const seen = new Set<string>();
  const uniqueSuggestions: string[] = [];
  for (const s of suggestionList) {
    if (!seen.has(s)) {
      seen.add(s);
      uniqueSuggestions.push(s);
    }
  }

  const rows = q.data ?? [];

  return (
    <div className="path-mapping-card">
      <div className="path-mapping-head">
        <span className="text-[13px] font-medium">Global mappings</span>
        <Pill>applies to every integration</Pill>
      </div>
      <div className="text-[11.5px] text-muted-2 -mt-1 mb-2 px-1 leading-relaxed">
        Applied AFTER any per-integration mapping. Useful when the
        same rewrite should affect Plex, Sonarr, Radarr, and every
        future integration at once.
      </div>
      {rows.length === 0 ? (
        <div className="text-[12.5px] text-muted italic py-2 px-1">
          No global mappings yet. Add one below.
        </div>
      ) : (
        <div className="path-mapping-rows">
          <div className="path-mapping-row path-mapping-row-head">
            <span title="The path as the integration reports it.">
              From
            </span>
            <span title="The same file as Auditarr's scanner sees it on this host.">
              To
            </span>
            <span />
          </div>
          {rows.map((r) => (
            <div key={r.id} className="path-mapping-row">
              <input
                className="settings-input mono"
                value={r.from_path}
                readOnly
              />
              <input
                className="settings-input mono"
                value={r.to_path}
                readOnly
              />
              <Button
                size="sm"
                variant="ghost"
                onClick={() => onDelete(r)}
                disabled={remove.isPending}
                title="Remove global mapping"
                aria-label="Remove global mapping"
              >
                <Icon name="trash" size={12} />
              </Button>
            </div>
          ))}
        </div>
      )}
      <div className="path-mapping-foot flex-wrap gap-2">
        {/* Stage 5: autocomplete via datalist driven by
            /system/path-suggestions. ``<datalist>`` lets the
            browser suggest known roots without losing free-text
            entry — operators with custom paths still type freely. */}
        <input
          className="settings-input mono"
          placeholder="From (e.g. /data/media)"
          value={newFrom}
          onChange={(e) => setNewFrom(e.target.value)}
          list="global-path-suggestions"
          aria-label="New global mapping: from path"
        />
        <input
          className="settings-input mono"
          placeholder="To (e.g. /mnt/storage)"
          value={newTo}
          onChange={(e) => setNewTo(e.target.value)}
          list="global-path-suggestions"
          aria-label="New global mapping: to path"
        />
        <datalist id="global-path-suggestions">
          {uniqueSuggestions.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        <Button
          size="sm"
          variant="accent"
          onClick={onAdd}
          disabled={create.isPending || !newFrom.trim() || !newTo.trim()}
        >
          {create.isPending ? "Adding…" : "Add global mapping"}
        </Button>
      </div>
    </div>
  );
}

// ── Stage 17 (audit follow-up): discovery + library dropdown ─────

/** Renders the per-integration discovery snapshot section with
 *  Mapped / Missing / Stale chips. Snapshot=null integrations show
 *  an admin-only "Discover now" button instead. Never auto-applies
 *  — every action goes through the operator. */
function DiscoverySection({
  integration,
  rows,
  onAddSuggestion,
}: {
  integration: PathMappingsIntegration;
  rows: { from: string; to: string }[];
  onAddSuggestion: (upstream_path: string) => void;
}) {
  const rediscover = useRediscoverPaths();
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";

  // Snapshot=null means "never discovered" — surface a Discover now
  // button for admins; for non-admins, nothing renders (the section
  // would be confusing without an actionable affordance).
  if (integration.discovered_paths == null) {
    if (!isAdmin) return null;
    return (
      <div className="path-mapping-discovery never-discovered">
        <span className="text-[11.5px] text-muted-2 flex-1">
          No discovery snapshot yet for this integration.
        </span>
        <Button
          size="sm"
          variant="ghost"
          disabled={rediscover.isPending}
          onClick={() =>
            rediscover.mutate(integration.integration_id, {
              onSuccess: () =>
                toast(`Discovered paths for ${integration.name}`, "ok"),
              onError: (err) =>
                toast(
                  `Discovery failed: ${(err as Error).message}`,
                  "error",
                ),
            })
          }
          title="Fetch the list of libraries from this integration"
        >
          <Icon name="refresh" size={12} />
          <span className="ml-1">
            {rediscover.isPending ? "Discovering…" : "Discover now"}
          </span>
        </Button>
      </div>
    );
  }

  // Compute the three states.
  const mappedFroms = new Set(
    rows.map((r) => normalizePath(r.from)).filter(Boolean),
  );
  const discoveredFroms = new Set(
    integration.discovered_paths.map((d) =>
      normalizePath(d.upstream_path),
    ),
  );

  const missing = integration.discovered_paths.filter(
    (d) => !mappedFroms.has(normalizePath(d.upstream_path)),
  );
  const stale = rows.filter(
    (r) =>
      r.from.trim() !== "" &&
      !discoveredFroms.has(normalizePath(r.from)),
  );

  // Nothing to surface → quiet card with just the snapshot count and
  // rediscover button for admins.
  if (missing.length === 0 && stale.length === 0) {
    return (
      <div className="path-mapping-discovery all-mapped">
        <span className="text-[11.5px] text-muted-2 flex-1">
          {integration.discovered_paths.length} discovered{" "}
          {integration.discovered_paths.length === 1 ? "library" : "libraries"}
          {" "}— all mapped.
        </span>
        {isAdmin ? (
          <Button
            size="sm"
            variant="ghost"
            disabled={rediscover.isPending}
            onClick={() =>
              rediscover.mutate(integration.integration_id, {
                onSuccess: () =>
                  toast(`Rediscovered ${integration.name}`, "ok"),
              })
            }
            title="Refresh the discovery snapshot"
          >
            <Icon name="refresh" size={12} />
          </Button>
        ) : null}
      </div>
    );
  }

  return (
    <div
      className="path-mapping-discovery has-gaps"
      data-testid="discovery-section"
    >
      <div className="flex items-center gap-2 mb-1.5">
        <span className="text-[11.5px] font-semibold text-muted">
          Discovery
        </span>
        <span className="text-[11.5px] text-muted-2">
          {missing.length} unmapped · {stale.length} stale
        </span>
        <span className="flex-1" />
        {isAdmin ? (
          <Button
            size="sm"
            variant="ghost"
            disabled={rediscover.isPending}
            onClick={() =>
              rediscover.mutate(integration.integration_id, {
                onSuccess: () =>
                  toast(`Rediscovered ${integration.name}`, "ok"),
              })
            }
            title="Refresh the discovery snapshot"
            aria-label="Rediscover paths"
          >
            <Icon name="refresh" size={12} />
          </Button>
        ) : null}
      </div>
      {missing.length > 0 ? (
        <div className="flex flex-col gap-1 mb-1.5">
          {missing.map((d) => (
            <div
              key={d.library_id}
              className="discovery-row discovery-missing"
              data-testid="discovery-missing"
            >
              <span className="text-[11.5px] font-medium">{d.label}</span>
              <code className="text-[11px] font-mono text-muted">
                {d.upstream_path}
              </code>
              <span className="flex-1" />
              <Button
                size="sm"
                variant="ghost"
                onClick={() => onAddSuggestion(d.upstream_path)}
                title="Pre-fill a mapping row with this path"
              >
                <Icon name="plus" size={12} />
                <span className="ml-1">Add mapping</span>
              </Button>
            </div>
          ))}
        </div>
      ) : null}
      {stale.length > 0 ? (
        <div className="flex flex-col gap-1">
          {stale.map((r, idx) => (
            <div
              key={`stale-${idx}`}
              className="discovery-row discovery-stale"
              data-testid="discovery-stale"
            >
              <code className="text-[11px] font-mono text-muted">
                {r.from}
              </code>
              <span className="text-[11.5px] text-muted-2">
                no longer in discovery
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** Local-path input (Sub-surface E) — text input with a library
 *  dropdown attached. Picking a library copies its ``root_path``
 *  into the input; the operator can still edit freely. */
function LocalPathInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (next: string) => void;
}) {
  const libraries = useLibraries();
  const libs = libraries.data ?? [];
  return (
    <div className="flex items-center gap-1">
      <input
        className="settings-input mono"
        placeholder="/mnt/storage/Movies"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      {libs.length > 0 ? (
        <select
          className="settings-input"
          aria-label="Copy a library root path into this field"
          value=""
          onChange={(e) => {
            const lib = libs.find((l) => l.id === e.target.value);
            if (lib?.root_path) {
              onChange(lib.root_path);
            }
            // Reset the select to its placeholder so picking the
            // same library again still fires the change handler.
            e.currentTarget.value = "";
          }}
          title="Pick a library to copy its root path"
        >
          <option value="">— library —</option>
          {libs.map((lib) => (
            <option key={lib.id} value={lib.id}>
              {lib.name}
            </option>
          ))}
        </select>
      ) : null}
    </div>
  );
}

/** Strip a trailing slash so "/a/b" and "/a/b/" compare equal —
 *  mirrors the backend's ``_strip()`` helper in path_mappings.py. */
function normalizePath(p: string): string {
  return p.trim().replace(/\/+$/, "");
}
