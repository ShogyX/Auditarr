# Auditarr v1.9 — Implementation Plan

**Source baseline:** `auditarr-1_8_3.zip`
**Target version:** `1.9.0`
**Plan author:** Planning instance (Claude Opus 4.7), 2026-05-17
**Executor:** Separate Claude Opus 4.7 instances, one chat per stage where practical

---

## 0. How this plan is meant to be used

The user has explicitly asked that:

1. **No code be typed in chat.** Every stage produces a zip on disk; the executor presents the zip via `present_files` and a one-line summary, nothing more.
2. **Each stage is self-contained.** A fresh executor instance picks up the previous stage's zip, reads `STATE.md` inside it, executes one stage, ships a new zip, updates `STATE.md`.
3. **Tests are mandatory.** Every stage that adds backend behavior adds at least one integration test or unit test against it. Every stage that adds frontend behavior adds at least one Vitest test. Existing tests must keep passing.
4. **Visual design is preserved.** Existing Tailwind/CSS tokens, component vocabulary (`Card`, `Pill`, `Button`, segmented tabs, `.files-table`, `.rules-table`, etc.) are reused. No new design system is introduced.
5. **No regressions.** Every stage ends with `pytest backend/tests` + `npm test --prefix frontend` green. If the executor cannot get to green it must hand back the failing stage rather than ship a half-broken zip.
6. **Context rotation.** After each stage the executor produces a `STATE.md` summarizing what's done, what's left, the next stage's exact entry conditions, and a manifest of files changed. A new chat can pick up from the zip alone — no chat-history reading needed.

---

## 1. What the plan delivers — issue → stage map

Every issue in `Issues_3.txt` maps to one or more stages. Stages are ordered to minimize churn (low-risk infra first, behavior changes next, large surfaces last).

| Issue (paraphrased) | Stage(s) |
|---|---|
| Scan progress bar missing in UI | **S1** — wire fix |
| Update apply hangs (DB-persisted failed apply) | **S1** — reaper |
| Plex/Jellyfin playback detection broken | **S6** — telemetry rewrite |
| Dedicated VT trigger in rule options | **S4** |
| Scope restriction for VT (extension/category/tags) | **S4** |
| Per-column searchable filters (Excel-style) | **S3** |
| Column size adjuster needs to be more visible | **S3** |
| Direct delete in Files tab (single + bulk) with confirmation | **S2** |
| Built-in rules stored identically to custom + Templates tab | **S4** |
| Subtitle list fields not accepting commas in rule editor | **S4** |
| Audio/subtitle language matching expansive (en/eng/English) | **S4** |
| Plex/Jellyfin compat built-in rules in 3 severities | **S4** |
| Rules trigger: search in Sonarr/Radarr/Bazarr | **S5** |
| And/Or selectable per condition (not rule-wide) | **S4** |
| Rule priority queue view inside editor | **S4** |
| Categories tab — drop graphs, add codec/sub/lang/etc. breakdown; mkv/mp4 labels; don't probe non-media | **S3** |
| Move path-mapping settings to Integrations tab | **S2** |
| All VT settings into VT integration tab | **S2** |
| Clean up Settings tab (remove duplicates) | **S2** |
| Tdarr / Plex integration script alignment | **S6** + **S8** |
| Playback insight fix (any play on Plex must be logged) | **S6** |
| Rule recommendations engine update | **S5** + **S9** |
| Tracearr playback-history integration | **S6** |
| Path-mapping + webhook-whitelist UI improvements (auto-discover, add buttons) | **S7** |
| Plex integration "unpolled" despite added | **S6** |
| Imported-tag filter from Sonarr/Radarr | **S7** |
| Logs page (per-service, sortable, exportable) | **S8** |
| Disable-card setting buggy | **S2** |
| GUI dynamic updates (no manual refresh) | **S2** + every stage thereafter |
| Documentation update (function/config only, no migration/stage talk) | **S10** |
| Reset Auditarr to fresh install | **S2** |
| Device-usage index → rule severity adaptation | **S9** |
| AI integration (Ollama / OpenAI / Anthropic / custom) for rule recommendations | **S9** |

---

## 2. Conventions every stage follows

### File layout & naming

- Plan files live under the repo root as `PLAN.md` and `STATE.md`.
- New backend modules go where the existing surface lives (don't introduce a new top-level package without explicit need). Example:
  - New API router: `backend/app/api/v1/<feature>.py`, wired in `app/api/v1/__init__.py`.
  - New service: `backend/app/services/<feature>/`.
  - New model: `backend/app/models/<feature>.py`, added to `app/models/__init__.py`.
  - New migration: `backend/migrations/versions/00NN_<slug>.py`, autoincrement the highest existing migration number.
- New frontend module follows feature folder convention: `frontend/src/features/<feature>/...`, with hooks in `frontend/src/hooks/use<Feature>.ts`.

### No "stage" or "migration" language in user-facing strings

Code comments may reference stages internally (the codebase already does this — keep that). **User-facing strings (UI copy, toasts, docs, settings descriptions, exception messages shown to the user) must be neutral**: describe what something does, not when it was added.

### Tests

- Backend: pytest with the existing `tests/integration` and `tests/unit` split. Use the existing `conftest.py` fixtures — don't invent new fixture infrastructure.
- Frontend: Vitest + React Testing Library. Use the existing `test-setup.ts`. New tests sit next to the source: `Foo.tsx` → `Foo.test.tsx`.
- Each stage's `STATE.md` ends with a "Verification" section: the two commands the next executor should run to confirm the previous stage didn't break.

### Database migrations

The codebase uses Alembic. Every schema change ships with one Alembic revision. Migrations must be:
- Idempotent on re-run (use `op.create_table(..., if_not_exists=True)` style where Alembic supports it; otherwise no-op the second invocation defensively).
- Reversible (provide a `downgrade()` even if the executor expects it'll never be used).

### Event bus & WebSocket

The event bus (`app.events.bus.EventBus`) and WS surface (`app.api.websocket`) already exist. New backend behaviors that mutate state and have a visible UI surface MUST emit an event so the WS bus propagates and the frontend's React-Query invalidation graph (`frontend/src/lib/invalidate.ts`) can refetch. This is the mechanism that fixes "GUI doesn't update dynamically".

### Visual design

Re-use what's there:
- Buttons: `Button` component, variants `accent | ghost | danger`, sizes `sm | md`.
- Cards: `Card` / `CardHead` / `CardBody`.
- Pills: `Pill sev="ok|info|warn|high|error|crit"`.
- Tab strips: `segmented` class with `role="tablist"` (already used in Settings and Rules).
- Tables: extend `files-table` / `rules-table` patterns. **Do not introduce a new table component.**
- Modals: existing dialog primitive from `components/ui/Dialog.tsx` if present (check before adding one).

If a new visual element is genuinely needed (e.g. a per-column searchable popover), it must be a thin extension of an existing one and live in `components/ui/`.

---

## 3. Stage breakdown

Each stage is sized so an executor instance can finish it without context exhaustion. Indicative sizes are in parentheses.

---

### Stage 1 — Plumbing fixes (small, fast feedback loop)

**Goal:** Fix the two bugs the user called out first (scan progress, hanging update apply), plus add infrastructure later stages depend on. No new UI features.

**1.1 — Scan progress reliability**
- Backend: emit `scan.progress` every **25** files (down from 100); emit explicitly on initial enumerate; emit a heartbeat every 5 s even when count hasn't changed (so the WS keepalive can detect a stuck scanner). Files: `backend/app/services/media/scanner.py`.
- Frontend: ensure `ScanProgressBar` is rendered in the AppShell header (not just FilesPage/Dashboard) so the bar is always visible during a scan. Files: `frontend/src/components/shell/AppShell.tsx`.
- Test (backend): integration test that triggers a scan against a synthetic 80-file library and asserts ≥3 `scan.progress` events emitted with monotonically non-decreasing `files_seen`.
- Test (frontend): unit test that mounts `ScanProgressBar`, dispatches three synthetic `scan.progress` WS events, asserts the bar updates without remounting.

**1.2 — Update-apply reaper**
- Backend: `UpdateApplyRepository.has_open()` must now check staleness — anything in `requested|running` for > `apply_timeout_seconds` (config, default 1800 s) is force-marked `failed` with `error="reaper: stale apply, host helper never reported back"`. Files: `backend/app/services/repositories/updater.py`, `backend/app/updater/service.py`.
- Surface a force-clear admin endpoint: `POST /api/v1/updater/applies/{id}/force-clear` (admin only) that flips a stuck row to `failed`. This is the operator escape hatch.
- Frontend: surface a "Last apply seems stuck — force-clear" button on the Updater page when the latest apply has been `requested` or `running` for > 5 minutes (UI hint; the backend's authoritative reaper runs every poll).
- Test: integration test that inserts a stale `UpdateApply`, calls `request_apply`, asserts no longer raises; calls force-clear endpoint, asserts the row transitions.

**1.3 — Invalidation graph audit**
- Walk every `useMutation` in `frontend/src/hooks/`. For each one missing `invalidateRelated()`, either add it or document why in the audit comment block at the bottom of `invalidate.ts`. Anchor: the existing Stage 13 audit comment (lines ~217-end of `invalidate.ts`).
- No code change to the graph itself unless a hole is found.
- Test: a Vitest smoke test that imports every hook module and asserts no top-level errors (catches imports / circular dep regressions).

**1.4 — Event-bus event-name registry**
- Backend: add `app/events/types.py` constants for every event name the codebase emits. Replace string literals like `"scan.progress"` with constants. This is the single place to look up "what events exist" for the WS docs and the AI-integration context payload (Stage 9).
- Test: import-smoke test asserts every event constant matches the regex `^[a-z]+\.[a-z_]+(\.[a-z_]+)?$`.

**Stage 1 deliverable:**
- Zip: `auditarr-1_9_0-stage1.zip`
- New backend tests under `tests/integration/test_scan_progress_v19_stage1.py`, `tests/integration/test_updater_reaper_v19_stage1.py`, `tests/unit/test_event_name_registry_v19_stage1.py`.
- Frontend test: `frontend/src/components/ui/ScanProgressBar.dynamic.v19s1.test.tsx`.
- `STATE.md` updated.

---

### Stage 2 — Settings cleanup, delete from disk, GUI refresh sweep

**Goal:** Tame the Settings page sprawl, make file deletion a first-class action, audit the "GUI doesn't update" complaint end-to-end.

**2.1 — Move path-mappings & webhook-whitelist to IntegrationsPage**
- Move `PathMappingsPanel` from `Settings → Integrations` sub-tab to the `/integrations` page top section.
- Remove the `integrations` sub-tab from SettingsPage entirely.
- Files: `frontend/src/features/integrations/IntegrationsPage.tsx`, `frontend/src/features/settings/SettingsPage.tsx`.
- Test: page renders, path-mapping rows appear on /integrations, do NOT appear on /settings.

**2.2 — VT settings consolidated on Integration row**
- Audit every VT setting currently under Settings → System → Runtime (search for VT/`virustotal` in `runtime_settings_schema.py`). Move display + editing onto the VirusTotal IntegrationRow expanded panel.
- The runtime backing rows remain (storage layer is shared) — only the UI surface moves.
- Files: `frontend/src/features/integrations/VirusTotalCard.tsx`, possibly a new `VirusTotalSettingsExpand.tsx`; remove the matching widgets from the Settings page.
- Test: VT settings no longer queryable on /settings; querying them from the integrations expand panel works.

**2.3 — Settings duplicate sweep**
- Grep audit `SettingsPage.tsx` for any setting now also exposed on Integrations. Remove the duplicate from Settings.
- Document the canonical home of every setting in a new file: `docs/settings/settings-map.md` (a flat table — operator's directory, also useful to the executor in future stages).

**2.4 — Direct delete in Files page**
- Backend: `DELETE /api/v1/media/{id}` (admin) and `POST /api/v1/media/bulk-delete` (admin) with body `{ "ids": [...], "remove_from_disk": bool, "reason": str|null }`. Both write an `AuditLog` entry per file. When `remove_from_disk=true`, files are moved into `data_dir/trash/<yyyy-mm-dd>/<uuid>/<original_path>` (re-use the Stage 05 trash convention already in `app.rules.schema.Delete`).
- Frontend: add `Delete` button to `FilesSelectionActions` and to `FileDetailDrawer`. Both open a confirmation dialog with: file name(s), severity preview, `remove_from_disk` checkbox (default OFF — index-only delete is the safe default), reason field (optional), and a typed-confirmation phrase for `remove_from_disk=true` mode (`type DELETE to confirm`).
- WS: emit `media.deleted` so the table refreshes immediately.
- Test (backend): single-id delete, bulk delete, file moved to trash, audit log row written.
- Test (frontend): confirmation dialog requires checkbox, selection clears after success.

**2.5 — Disable-card setting fix**
- The `dashboardDisabled` store key already exists. Audit `DashboardCardMenu.tsx` and every card to verify the "disable" affordance is wired uniformly (some cards may early-return on `disabled` from the store, others may not).
- Add a single helper: `useDashboardCardDisabled(cardKey)` that returns `boolean` and a `setDisabled` setter. Convert every card to use it.
- Test: toggle disable for each card, assert it disappears, refresh page, still disappears.

**2.6 — Factory-reset endpoint**
- Backend: `POST /api/v1/system/factory-reset` (admin) with body `{ "confirm_phrase": "reset auditarr" }`. Truncates every table except `users` (we keep the admin so the operator can log back in) and `auditarr_meta`. Empties `data_dir/trash/`. Removes runtime overrides. Writes an `AuditLog` with reason `factory_reset`.
- Frontend: `SystemMaintenanceCard` grows a "Factory reset" button at the bottom inside a `<details>` block so it's not immediately visible. Two-step: button → modal → typed phrase → final action.
- Test: factory reset wipes media/rules/integrations, keeps users, audit-log row written.

**Stage 2 deliverable:**
- Zip: `auditarr-1_9_0-stage2.zip`
- New tests cover delete, factory reset, dashboard disable wiring.
- `STATE.md` updated.

---

### Stage 3 — Tables & Categories overhaul

**Goal:** Make the tables Excel-grade. Make Categories useful instead of pretty.

**3.1 — Per-column searchable filters (Excel-style)**
- Each filterable column header gets a small filter icon. Click → popover with:
  - Search input
  - "Include" / "Exclude" mode toggle
  - Checkboxed list of distinct values (the popover queries a new backend endpoint: `GET /api/v1/media/distinct?field=<col>&library_id=<id>&prefix=<search>` returning top 200 unique values + counts)
- Generalize beyond the current `filename | codec | container | extension`. Add: `severity`, `library`, `audio_codec`, `subtitle_codec`, `tags`, `width/height`, `framerate`.
- Same affordance on the Rules table.
- Files: new component `frontend/src/components/ui/ColumnFilterPopover.tsx`; `frontend/src/features/files/FilesTable.tsx`; `frontend/src/features/rules/RulesTable.tsx`; backend `backend/app/api/v1/media.py`.

**3.2 — Column resizer visibility**
- The drag handle is currently a 4px hit area with no visual indication. Make it visible on header hover (1px vertical accent rule) and on drag (cursor changes app-wide). Re-use Excel's pattern: the entire column resize handle shows a vertical guide line during drag.
- Files: `frontend/src/components/ui/ResizableHeaderCell.tsx`, `frontend/src/styles/components.css`.

**3.3 — Categories card redesign**
- Drop the bar-graph rows. Replace with a structured panel:
  - **Resolutions** row: counts of `<480p, 720p, 1080p, 1440p, 4K, 8K, other>`
  - **Extensions** row: top 8 extensions by file count + size
  - **Containers** row: with normalized labels (`matroska` → `MKV`, `mov,mp4,m4a,3gp,3g2,mj2` → `MP4`)
  - **Subtitle formats** row: SRT / ASS / VOBSUB / PGS / etc.
  - **Subtitle languages** row: count per language
  - **Audio languages** row: count per language
  - **Unknown tracks** row: `audio_unknown_count`, `video_unknown_count`
  - **Internal vs external subtitles** row
  - **Orphan count**
  - **Median bitrate per (resolution, codec, container, library)** — a small table
- Backend extension: `GET /api/v1/dashboard/composition?library_id=<id>` returns this whole payload in one call.
- ffprobe gating: files where `classify(filename).category != "media"` are NOT probed and not flagged as "unprobed". This is in `services/media/scanner.py` and `services/media/classify.py`; verify the existing `should_probe()` check, fix if leaky.
- Files: backend new `app/services/dashboard/composition.py`; frontend rewrite `features/dashboard/CategoriesCard.tsx`.

**3.4 — Container label normalization (everywhere)**
- A small util: `frontend/src/lib/containerLabel.ts` (and a Python counterpart `app/utils/container_label.py`) — `matroska` → `MKV`, `mov,mp4,m4a,3gp,3g2,mj2` → `MP4`, `matroska,webm` → `MKV`. Used by Categories, Files table, FileDetailDrawer.

**3.5 — "Unprobed" classification fix**
- Make sure non-media files (`.nfo`, `.jpg`, `.srt`, sidecar text files) never appear in unprobed counts on the Categories card or anywhere else. Adjust `services/dashboard/stats.py` and the new composition service.

**Stage 3 deliverable:**
- Zip: `auditarr-1_9_0-stage3.zip`
- Tests: new column-filter popover Vitest test; new composition endpoint integration test; classification gating unit test.
- `STATE.md` updated.

---

### Stage 4 — Rules engine vocabulary & built-in templates

**Goal:** Make rules expressive enough for the cases the user listed. Make built-ins editable templates.

**4.1 — Language matching normalization**
- Add normalizer: `app/rules/language_normalize.py`. Folds `en | eng | english | ENGLISH | en-US | en_GB` → `en`. Operates on both rule values and file values at evaluation time. Same applies to `subtitle_languages` and `audio_languages`.
- The frontend rule editor surfaces a Language picker (auto-complete with ISO 639-1/-2 + common aliases) for these fields.
- Test: a rule matching `audio_languages` contains `en` matches files tagged `eng`, `English`, `en-US`.

**4.2 — Comma-separated list inputs accept commas (rule editor)**
- The Visual Rule Builder's `subtitle_languages` / `audio_languages` / `tags` inputs currently reject commas. Convert to either a multi-tag chip input (preferred — re-use the pattern from notifications channel "to" field if present) or a textarea that splits on commas + trims whitespace.
- File: `frontend/src/features/rules/VisualRuleBuilder.tsx`.

**4.3 — Per-condition AND/OR**
- Schema today supports nested AllOf/AnyOf but the UI exposes them only as rule-wide groups. Update the builder to:
  - Each row has a leading combinator dropdown (AND / OR) that switches the parent group's type.
  - Group nesting via "Group" button (already there in pattern, just expose it).
- Schema is unchanged; this is pure UI.
- File: `frontend/src/features/rules/VisualRuleBuilder.tsx`.

**4.4 — Built-in rules as templates**
- Today: built-ins are seeded with `is_builtin=true`. Operators can disable but not edit.
- New behavior:
  - Built-ins still seed on startup, but as **templates** in a new table `rule_templates`. Templates are reference material — they don't evaluate against media on their own.
  - The Rules page grows a new sub-tab: **Templates**. Lists every shipped template. Each row has Duplicate / Restore-as-rule actions.
  - When an operator clicks "Use template", a normal `Rule` row is inserted with `is_builtin=false`, body copied from the template. The operator can then edit it freely.
  - "Restore deleted built-ins" is a single action that resets every template to the shipped definition (no migration needed — startup re-seeds them anyway).
  - Existing `is_builtin=true` rule rows are converted to `is_builtin=false` (operator-owned copies) in a migration so no one loses behavior they relied on.
- Files: new model `app/models/rule_template.py`, migration, repo, API at `app/api/v1/rule_templates.py`, frontend page tab.

**4.5 — Rule priority queue visible inside editor**
- The rule editor grows a side panel "Evaluation order": lists every enabled rule sorted by priority, highlights where the current rule sits. Click a row to jump to that rule.
- File: `frontend/src/features/rules/RuleEditorPage.tsx` (already exists, extend it).

**4.6 — VT trigger as rule action + VT scope restriction**
- New action type in the rule schema: `{ "type": "vt_lookup" }`. Triggers a VT lookup for files matching the rule. Useful for scoped lookups (e.g. "only .exe + .iso files" — though Auditarr is media-focused, the use case is downloaded sidecars / archives).
- VT scope restriction in the VT integration config:
  - `vt_scan_extensions: list[str]` (default: empty → all)
  - `vt_scan_categories: list[str]` (default: `["media"]`)
  - `vt_scan_required_tags: list[str]` (default: empty)
- The scanner's existing `enqueue_for_vt_lookup` call respects these scope rules before enqueuing.
- Files: `backend/app/rules/schema.py`, `backend/plugins/virustotal/backend.py`, `backend/app/services/media/scanner.py`.

**4.7 — Built-in Plex/Jellyfin compat rules (3 severities)**
- Add three new templates in `app/rules/builtin.py`:
  - **"Likely transcode"** (severity `warn`): HEVC 10-bit, HEVC 1080p+ on older clients, AC3 5.1 audio.
  - **"Always transcode"** (severity `high`): HDR10/Dolby Vision without a compatible target client, 4K HEVC, DTS-HD MA.
  - **"Unplayable / Unsupported"** (severity `crit`): MPEG-2 video in MP4, Bink, Vorbis in MP4, etc.
- These ship as templates (per 4.4), so operators see them in the Templates tab and can use them as starting points.

**Stage 4 deliverable:**
- Zip: `auditarr-1_9_0-stage4.zip`
- Tests: language normalization unit, VT scope filter, rule_templates API, new built-ins valid.
- `STATE.md` updated.

---

### Stage 5 — Cross-integration rule actions (search in Sonarr/Radarr/Bazarr)

**Goal:** Let rules trigger actions on integrations.

**5.1 — `search_upstream` rule action**
- New action: `{ "type": "search_upstream", "target": "sonarr|radarr|bazarr", "integration_id": "..." }`.
- When the action fires, the rule engine pushes a job onto the worker. The worker resolves the integration, looks up the upstream id (via the existing tag/path linkage), and calls the integration provider's new `trigger_search(media_file)` method.
- Each provider grows a `trigger_search` method:
  - Sonarr: `POST /api/v3/command { "name": "SeriesSearch", "seriesId": ... }`
  - Radarr: `POST /api/v3/command { "name": "MoviesSearch", "movieIds": [...] }`
  - Bazarr: `GET /api/episodes/wanted?seriesid=...` then `POST /api/episodes/subtitles`
- Files: `backend/app/rules/schema.py`, `backend/plugins/{sonarr,radarr,bazarr}/backend.py`, evaluator hook in `backend/app/rules/evaluator.py`.

**5.2 — Rule editor UI for the new action**
- The visual builder's action dropdown now offers "Search in upstream". Picking it shows two dropdowns: target (Sonarr/Radarr/Bazarr) and integration (filtered to enabled integrations of that kind).
- File: `frontend/src/features/rules/VisualRuleBuilder.tsx`.

**5.3 — Audit + WS events**
- The action emits `rule.action.search_upstream` with the integration id, media file id, and upstream response status. Surfaced in the Audit log and in the Rule's "Recent activity" timeline.

**5.4 — Tests**
- Provider unit tests for `trigger_search`.
- Integration test: rule fires → action enqueued → worker handles it → mock provider's `trigger_search` is called once.

**Stage 5 deliverable:**
- Zip: `auditarr-1_9_0-stage5.zip`
- `STATE.md` updated.

---

### Stage 6 — Playback telemetry rewrite + Tracearr integration

**Goal:** Fix the "Plex shows unpolled" / "playback insight broken" complaints. Add Tracearr as a playback-data source.

**6.1 — Adopt the resilient Plex client patterns from `plex.txt`**
- Wire the user-supplied Plex script's lessons into the existing `plugins/plex/backend.py`:
  - Add a `diagnostics()` method that runs the four sanity checks (`/`, `/library/sections`, `/activities`, optimize endpoint reachability) and stashes the result in the integration's `health_metadata`. Surface this in the IntegrationRow.
  - Add file→ratingKey resolution that doesn't cache persistently (current code may cache; if so, gate it behind a TTL so renamed files don't go stale).
  - Add the verification helpers (`verify_optimization_started`, `verify_optimization_completed`) and call them after `submit_transcode_job` when `routing_target=plex`.
- Files: `backend/plugins/plex/backend.py`.

**6.2 — Plex playback poller — "unpolled" fix**
- Audit the integration-health "unpolled" pill source. The pill currently reads from `Integration.health_metadata.last_poll_at`. Find why polling never advances it for the user's case:
  - Possibility A: cursor never advances when there are zero events.
  - Possibility B: the dashboard reads a different field than the poller writes.
  - Possibility C: the cron schedule for `playback_poll` isn't running or is gated by an off-by-default feature flag.
- Fix root cause; add an integration test that runs the poller once with zero events and asserts `last_poll_at` advances.
- Surface a new field on the integration page: "Last polled" with a relative timestamp.

**6.3 — "Any play must be logged" — live + history**
- Today: the poller pulls `/status/sessions/history/all`. Live sessions are pulled separately and not always persisted.
- Add: live `/status/sessions` snapshot is dedup-joined into `PlaybackEvent` on the next poll if the upstream history hasn't reflected it yet. Each live session that crosses a "completed enough" threshold (>= 30s OR `>= 90% viewOffset`) gets a synthetic history row.
- Same logic for Jellyfin (`/Sessions` + `/Sessions/Playing`).
- File: `backend/app/services/playback/poller.py` + provider `fetch_live_playbacks`.

**6.4 — Tracearr integration plugin**
- New plugin: `backend/plugins/tracearr/`.
  - `manifest.json` declares it as `kind: "tracearr"`, integration shape similar to Sonarr/Radarr.
  - `backend.py` implements `IntegrationProvider`:
    - `healthcheck` — pings `/api/health`
    - `fetch_playback_events(since)` — pulls Tracearr's playback history endpoint
    - No transcode submission
  - The events flow into the same `PlaybackEvent` table with `source="tracearr"`.
- The dashboard's playback panels include Tracearr-sourced events automatically.
- Files: new plugin folder, frontend integration row supports the new kind.

**6.5 — Playback dashboard reliability**
- The PlaybackStatsCard breaks if backend returns partial data. Audit and harden — null-safe rendering, retry on transient errors.

**Stage 6 deliverable:**
- Zip: `auditarr-1_9_0-stage6.zip`
- Tests: Plex diagnostics method, poller-no-event timestamp advance, Tracearr plugin lifecycle.
- `STATE.md` updated.

---

### Stage 7 — Integration UX polish (auto-discovery inputs, tag filter)

**Goal:** Replace the janky text-area inputs for path mappings & webhook whitelist with structured UI. Add tag filter from Sonarr/Radarr.

**7.1 — Path-mapping & webhook-whitelist structured inputs**
- Replace the comma-separated textarea with a chip-list UI:
  - Each entry is a row with two fields (path-from, path-to) plus a delete button.
  - "+ Add" appends a blank row.
  - "Auto-discover" button calls a new backend probe: `POST /api/v1/integrations/{id}/discover-path-mappings` which:
    - For Sonarr/Radarr/Bazarr: queries the upstream root-folders endpoint, suggests mappings between observed paths and Auditarr's library roots.
    - For Plex/Jellyfin: similar (library section root paths).
- Webhook whitelist gets the same treatment: chip-list of CIDRs/IPs, "Auto-discover from recent webhook deliveries" (reads the audit log for `webhook.received` from this integration in the last 24h).
- Files: `frontend/src/features/integrations/IntegrationRow.tsx`; new components `PathMappingEditor.tsx`, `IpWhitelistEditor.tsx`; new backend endpoint.

**7.2 — Tag filter for Sonarr/Radarr/Bazarr**
- Integration config grows `tag_allowlist: list[str]` (empty = all tags accepted) and `tag_denylist: list[str]`.
- The tag-sync importer filters before insert/update; tags removed by edit are deleted from media on the next sync.
- UI: a chip-list under the integration's expand panel. Auto-discover button pulls `GET /api/v3/tag` from the upstream and shows available tags.
- Files: `backend/app/integrations/tag_sync.py`, new config fields, frontend chip UI.

**Stage 7 deliverable:**
- Zip: `auditarr-1_9_0-stage7.zip`
- Tests: discovery endpoints (mocked HTTP), tag-allowlist filtering, chip-input form behavior.
- `STATE.md` updated.

---

### Stage 8 — Logs page + Tdarr handoff polish

**Goal:** Visibility into running services. Polish Tdarr integration using the user-supplied script's patterns.

**8.1 — Logs page**
- Backend: log capture. The codebase uses `structlog`. Add an in-memory ring buffer (`app/core/log_buffer.py`) — last N=5000 log records, keyed by category. Also a file-tailing fallback (`/var/log/auditarr/<service>.log` if present) so journalctl-style consumers work.
- New API: `GET /api/v1/system/logs?service=<api|worker|scheduler|all>&since=<ts>&level=<info|warn|error>&limit=200`. Pagination cursor.
- New API: `GET /api/v1/system/logs/export?service=...&since=...` returns NDJSON for the operator to save.
- Frontend: new page `/system/logs`, table with service filter, level filter, time range, search. Auto-tail toggle. Export button → triggers the NDJSON download.
- Surface unhandled errors as a red dot on the sidebar's "System" entry when the backend has detected a service error in the last 5 minutes.
- Files: new `backend/app/core/log_buffer.py`, `backend/app/api/v1/system_logs.py`; new `frontend/src/features/system/LogsPage.tsx`.

**8.2 — Tdarr handoff alignment**
- Walk the user-supplied `tdarr.txt` and the existing `backend/plugins/tdarr/backend.py`. Apply:
  - Stack selection by heuristic score (the existing code does this loosely; tighten the matching to match the user's `score_stack` algorithm).
  - Output naming convention via the user-supplied `build_output_name`.
  - Optional `watch` mode (poll Tdarr until job completes) wired into the worker — the polling-loop is already partially there in `services/optimization/poller.py`; ensure it terminates correctly when Tdarr reports the job as `completed` or `error`.
- Files: `backend/plugins/tdarr/backend.py`, `backend/app/services/optimization/poller.py`.

**Stage 8 deliverable:**
- Zip: `auditarr-1_9_0-stage8.zip`
- Tests: logs API, log export, Tdarr stack-selection heuristic unit.
- `STATE.md` updated.

---

### Stage 9 — Rule recommendations engine + Device index + AI integration

**Goal:** The big-ticket "smart" features the user wants.

**9.1 — Device index**
- New model `app/models/playback_device.py`:
  - `id`, `name` (e.g. "Living Room Apple TV"), `client_identifier`, `platform`, `product`, `device_model`, `first_seen_at`, `last_seen_at`, `playback_count`, `transcode_count`, `direct_play_count`, `direct_stream_count`.
- The playback poller upserts a device row on every event it ingests.
- New API: `GET /api/v1/playback/devices`. New dashboard card: "Devices observed" — top 10 by play count, transcode ratio bar per device.
- Files: model, migration, repo, API, dashboard card.

**9.2 — Rule recommendations refresh**
- The existing analyzer (`app/services/playback/analyzer.py`) emits `RuleSuggestion` rows. Extend:
  - Use device-index data: only suggest a Plex/Jellyfin compat rule for codecs that a watched device has actually transcoded. (Don't suggest "transcode HEVC" if every device direct-plays HEVC.)
  - Optimization-profile suggestions: when the same `(video_codec, audio_codec, container)` triple keeps showing up in transcoded sessions, suggest a profile that pre-converts it.
  - Bazarr-search suggestions: when a file repeatedly plays without subtitles and the operator has Bazarr connected, suggest a rule that triggers a Bazarr search.
  - Removal/rollback suggestions: if an active rule hasn't matched any file in the last 30 days AND no device usage changed, suggest dismissing it. If a rule fires aggressively against direct-play-friendly files, suggest lowering its severity.
- All suggestions are still operator-approved; nothing auto-deploys.

**9.3 — AI integration**
- Two pieces:
  - **AI provider integration kind**: `ollama | openai | anthropic | custom_openapi`.
    - Stored as a regular `Integration` row (`kind="ai_provider"`).
    - Config: `endpoint`, `model`, `api_key` (secret), `temperature`, `max_tokens`.
    - Provider unit: backend module per kind, all conforming to a `chat(messages: list[dict])` interface.
  - **Suggestion-generator endpoint**: `POST /api/v1/rule-suggestions/ai-generate` (admin). The endpoint:
    - Picks the enabled AI provider (operator selects on the page if more than one).
    - Builds a context payload: top 50 files by total transcode count, all currently-active rules, library size summary, device usage summary, available actions/operators/fields from the rule DSL.
    - System prompt forbids duplicate suggestions and bad ideas (a clear "what NOT to do" list per the user's request).
    - Output: structured JSON with one or more proposed `RuleDefinition`s, each validated against `rules.schema` before persisting as `RuleSuggestion` rows with `heuristic="ai_<provider>"`.
- The dashboard suggestions card surfaces AI suggestions alongside heuristic ones, with an "AI" badge.
- Operators can mark suggestions as bad → the next AI call includes the rejection list in the prompt.
- Files: new `backend/app/services/ai/`, providers in `backend/app/services/ai/providers/`, new API, frontend integration row support.

**9.4 — Privacy & cost guards**
- File paths sent to external AI providers are anonymized (replace library root with `<library>/`). Operator can opt out of external send entirely (Ollama / custom-local-endpoint are still allowed).
- Per-call token cap (`max_tokens`) and per-day call budget (`ai_call_budget`) on the integration. Exceeded → fall back to heuristic suggestions and surface a banner.

**Stage 9 deliverable:**
- Zip: `auditarr-1_9_0-stage9.zip`
- Tests: device upsert from poll, analyzer-with-devices unit, AI provider unit (mocked HTTP), AI suggestion round-trip integration.
- `STATE.md` updated.

---

### Stage 9.5 — UX overhaul (operator-reported)

**Goal:** Address the operator-supplied UX findings collected during the v1.9 audit pass (OP-1, OP-2, OP-3, OP-5, OP-7, OP-8, OP-9). These are not bug fixes — the v1.9 audit-pass deliverable already shipped the surgical fixes. This stage is the substantial frontend work the audit triage explicitly deferred ("substantial UX work, recommend own session").

**Scope is frontend-heavy, with a small set of backend extensions for OP-7/8/9 (preferred-language settings, foreign-without-subs counter, incompatible-media counter, Mbps + median sort columns on the categories endpoint).** Backend tests pin the new endpoints; frontend tests pin the new components. The existing 2,346-test surface stays green throughout.

The stage's 7 substages can ship as a single deliverable OR (recommended) split into 9.5.A (layout + space sweep) and 9.5.B (nested editor + dashboard surfaces). Splitting halves the per-session risk profile and matches the natural seam between "make existing pages bigger" and "build new things."

---

**9.5.1 — Rules page full-screen layout (OP-1)**

Current: `RulesPage.tsx` renders two stacked cards (Rules table + side panel) inside a constrained container, using ~half the viewport on a wide screen. Operators with 20+ rules scroll constantly.

Target: the rules surface uses the full viewport on ≥xl breakpoints. The rule list and side panel sit side-by-side on wide screens, stacked on narrow ones (existing mobile behaviour preserved).

- Replace the constrained container with a full-bleed flex layout: `flex-col xl:flex-row` with the rule list at `xl:basis-2/3` and the side panel at `xl:basis-1/3`.
- Remove the redundant page-padding wrapper inherited from the legacy modal layout; the page header already has its own padding.
- The Templates tab inherits the same shell.

**Files:** `frontend/src/features/rules/RulesPage.tsx`, `frontend/src/features/rules/RulesTable.tsx` (column-width tweaks for the wider container), CSS rule for the shell.

**Tests:** existing `RulesTable.resize.test.tsx` extended to assert the wider layout renders at ≥xl viewport; existing 8-test editor suite stays green.

---

**9.5.2 — Nested AND/OR rule editor (OP-2)**

Current: `RuleEditorBody` toggles ALL conditions globally between AND/OR. Operators wanting `(A AND B) OR (C AND D)` must drop to the JSON tab.

Target: visual editor surfaces nested groups. Each group is itself AND or OR; conditions and sub-groups freely mix.

This is the largest single change in the stage — estimate 300-500 LOC across the editor, the renderer, and tests.

- New component `frontend/src/features/rules/ConditionGroupEditor.tsx`:
  - Renders an `all`/`any`/`not` group as a card with a header (AND/OR pill + add-condition + add-group buttons) and a body (children).
  - Children are either condition rows (existing component, reused) or nested `ConditionGroupEditor` instances.
  - Drag-to-reorder within a group (optional; if scope-cut, defer to follow-up). At minimum, up/down arrows.
- The `RuleDefinition.match` shape already supports `all`/`any`/`not` per `rules.schema`. Editor's serializer walks the tree.
- The Stage 4.5 side-panel rule preview already renders nested groups (it consumes the same `RuleDefinition`); confirm it still reads correctly.

**Backend:** zero changes — the DSL already supports nested composites.

**Files:** new `ConditionGroupEditor.tsx`, new `ConditionGroupEditor.test.tsx`, edits to `RuleEditorBody.tsx` (replace flat conditions list with a root group), edits to `useRuleEditorState.ts` (track nested groups as part of `definition`).

**Tests (≥12 new):**
- Adding a nested group produces correct serialized JSON.
- Removing a nested group cleans up children.
- Mixed AND/OR roundtrips losslessly (load → edit → save → load).
- `not` group: single child enforced.
- Maximum depth (set to 5) refuses additions past the cap with a clear message.
- Dry-run a nested rule and confirm the same match output as the equivalent flat rule.

---

**9.5.3 — Rule evaluation panel resize (OP-3)**

Current: the rule evaluation side panel cramps text — code-like definitions wrap awkwardly into narrow columns.

Target: panel is at least 400px wide on xl viewports, with proper word-break for monospaced definition text.

- Side-panel CSS: `min-w-[400px]` (xl), `min-w-[320px]` (md), full-width on narrow.
- Definition viewer uses `break-all` + `whitespace-pre-wrap` so long codec strings wrap without horizontal scroll.
- Existing evaluation list rows: expand vertical padding, drop `text-xs` in favor of `text-[13px]` for the row title.

**Files:** `frontend/src/features/rules/RuleSidePanel.tsx` (or wherever the eval panel lives), CSS adjustments.

**Tests:** existing tests preserved; one new test asserts the panel min-width on xl.

---

**9.5.4 — Disabled rule styling polish (already shipped in v1.9 audit, OP-4)**

**Status: SHIPPED in v1.9 audit deliverable.** Documented here only so the Stage 9.5 checklist accurately reflects which OP findings are closed before this stage runs.

The audit pass already:
- Dropped disabled-row opacity from 0.65 → 0.4, added `grayscale(60%)`.
- Added a "Custom" badge alongside the existing "Built-in" badge.
- Pinned the styling via `RulesTable.resize.test.tsx`.

Nothing to do in this stage.

---

**9.5.5 — Frontend-wide space utilization sweep (OP-5)**

Current: pages constrained to ~half viewport with everything stacked vertically. The Integrations page is the worst offender — multiple narrow cards stacked under each other when a 2-column grid would fit.

Target: pages widen to the natural content + adopt multi-column grids where multiple cards of similar weight stack today.

Pages to sweep, in priority order:
1. **Integrations page** — 2-column grid on xl. Each integration row spans one column.
2. **Settings page** — multi-section, currently a single narrow column. Group into 2-column grid where settings are independent.
3. **Plugins page** — list of plugins gets a 2-column card grid on xl.
4. **Optimization page** — queue tile + profiles tile + recent-runs tile in a 3-column layout on xl.
5. **Dashboard** — already grid-aware; verify cards adopt 3-column on 2xl breakpoint (1920px+) for ultra-wide setups.

Per-page CSS work, no logic changes. Each page gets a one-test viewport assertion (xl grid renders 2+ columns).

**Files:** `frontend/src/features/integrations/IntegrationsPage.tsx`, `frontend/src/features/settings/SettingsPage.tsx`, `frontend/src/features/plugins/PluginsPage.tsx`, `frontend/src/features/optimization/OptimizationPage.tsx`, `frontend/src/features/dashboard/DashboardPage.tsx`.

**Tests:** one viewport test per page (5 new total).

---

**9.5.6 — Categories card upgrade (OP-7)**

Current: dashboard Categories card shows resolution/language/container rows with kbps bitrate. Rows aren't clickable; bitrate is in kbps only; median bitrate breakdown isn't sortable.

Target:
- Each row is a deep-link to `/files?<filter>` (codec / container / resolution / language as a query param the Files page already understands).
- Bitrate column shows both **Mbps** and **kbps** (Mbps primary, kbps in muted secondary text).
- Median-bitrate breakdown table is sortable by codec name, file count, or median bitrate. Default sort: median bitrate descending.

**Backend extensions:**
- `GET /api/v1/dashboard/categories` already returns the breakdown — no new fields needed.
- `GET /api/v1/dashboard/bitrate-matrix` (the median surface) — extend the response shape to include `median_bitrate_mbps` alongside the existing `median_bitrate_kbps` (calculated server-side from the same row data, avoids client-side division ambiguity).

**Frontend:**
- `CategoriesCard.tsx`: rows become `<Link>` elements pointing at `/files?codec=hevc` etc. The Files page already filters on these query params (verify by reading the Files page hook).
- New `useState` in `BitrateMatrix.tsx` (or wherever the matrix renders) for sort key + direction. Click a column header to sort; arrow icon shows direction.
- Mbps formatting: `(kbps / 1000).toFixed(1)` rendered as `12.5 Mbps`; kbps as `12,500 kbps` muted secondary.

**Files:** `backend/app/api/v1/dashboard.py`, `backend/app/schemas/dashboard.py`, `frontend/src/features/dashboard/CategoriesCard.tsx`, frontend bitrate-matrix component.

**Tests:**
- Backend: `bitrate-matrix` response shape includes `median_bitrate_mbps` (1 new test).
- Frontend: Categories row click navigates to `/files?...` (1 new test); sort flip on bitrate matrix swaps order (1 new test).

---

**9.5.7 — Foreign audio without preferred subtitles surface (OP-8) + incompatible-media surface (OP-9)**

Current: no dashboard surface for either signal.

Target: two new dashboard tiles, with operator-configurable settings.

**New settings:**
- `preferred_audio_languages: list[str]` (default `["eng"]`)
- `preferred_subtitle_languages: list[str]` (default `["eng"]`)

Stored as JSON in the existing `settings` table.

**Backend extensions:**

`GET /api/v1/dashboard/foreign-audio` — returns:
```json
{
  "count": 42,
  "sample_ids": ["...", "...", "..."],   // first 10 for the "see all" link
  "preferred_audio_languages": ["eng"],
  "preferred_subtitle_languages": ["eng"]
}
```

A file qualifies if:
- Its primary audio track's language is NOT in `preferred_audio_languages`, AND
- It carries no subtitle track in any of `preferred_subtitle_languages` (English audio file with French subs counts as "no preferred subs" if `preferred_subtitle_languages = ["eng"]`).

`GET /api/v1/dashboard/incompatible-media` — returns:
```json
{
  "count": 17,
  "sample_ids": [...]
}
```

A file qualifies if any enabled rule with an `incompatible_audio` or `incompatible_video` action matched it (re-read via the existing `rule_evaluations` table). The matching rules are configured by the operator via the existing rule editor — this surface just counts files where ANY such rule fired.

**Frontend:**
- Two new dashboard tiles, sized to match existing cards.
- Each tile shows: count, an explanatory subtitle, a "view files" CTA that deep-links into Files filtered by tag (rules add tags like `foreign-no-subs` / `incompatible-audio`).
- New settings page section: "Language preferences" — two multi-select inputs, default values pre-populated, save invalidates the dashboard queries.

**Files:**
- Backend: `backend/app/api/v1/dashboard.py` (2 new endpoints), `backend/app/core/settings.py` (2 new fields), `backend/app/services/dashboard/foreign_audio.py` (new), `backend/app/services/dashboard/incompatible.py` (new), migration if a `settings` model row is needed.
- Frontend: `frontend/src/features/dashboard/ForeignAudioCard.tsx` (new), `frontend/src/features/dashboard/IncompatibleMediaCard.tsx` (new), `frontend/src/features/settings/LanguagePreferences.tsx` (new), wiring into `DashboardPage.tsx` and `SettingsPage.tsx`.

**Tests:**
- Backend: 4 new integration tests (each endpoint × empty library + populated library + settings reflected).
- Frontend: 2 new component tests (each card renders count + CTA links correctly).

---

**Stage 9.5 deliverable:**
- Zip: `auditarr-1_9_0-stage9_5.zip` (or two zips if split: `stage9_5a` + `stage9_5b`)
- Tests added: ≥25 new (12 nested editor + 5 viewport + 3 categories + 4 dashboard surfaces + minor)
- `STATE.md` updated with each substage's status (some can defer if scope is tight).
- All operator findings OP-1, OP-2, OP-3, OP-5, OP-7, OP-8, OP-9 either shipped or explicitly documented as deferred to Stage 10 with a clear reason.

**Notes for the executor:**
- The nested-editor substage (9.5.2) is the single highest-risk piece. Recommend implementing in isolation first, with a draft branch reviewed before merging into the stage zip.
- The space-utilization sweep (9.5.5) is mostly CSS — small diffs per file, but many files touched. Worth a focused half-session.
- The two new dashboard surfaces (9.5.7) double as the Stage 10 "documentation rewrite" demonstration material — they're new pages worth doc coverage.
- Backend changes are small enough that no new migration is strictly required if `settings` already has a JSON column for arbitrary preferences. Confirm before adding a migration.

---

### Stage 10 — Documentation rewrite, neutral language sweep, release prep

**Goal:** Rewrite docs to reflect 1.9 surface, scrub stage/migration/internal language, prep release.

**10.1 — Neutral-language sweep (all `docs/*.md`)**
- For every `.md` under `docs/`: remove references to "Stage NN", "migration NNNN", "v1.7 addendum", "Issue NN", "audit follow-up", and similar internal markers. Replace with descriptions of behavior.
- Documentation tells the operator **what** and **how**, never **when** or **why historically**.

**10.2 — Update docs for new features**
- Files / endpoints / pages added in Stages 1-9 each get a doc page or doc section.
- Particularly:
  - `docs/files/delete.md` — direct delete flow
  - `docs/dashboard/categories.md` — new categories card
  - `docs/dashboard/devices.md` — device index
  - `docs/dashboard/ai-suggestions.md`
  - `docs/integrations/tracearr.md`
  - `docs/integrations/ai-providers.md`
  - `docs/rules/templates.md`
  - `docs/rules/search-upstream.md`
  - `docs/system/logs.md`
  - `docs/system/factory-reset.md`

**10.3 — README + CHANGELOG**
- `README.md` updated to reflect 1.9 surface.
- `CHANGELOG.md` gets a `## [1.9.0] — <date> — Operator-quality release` section that lists the changes in operator-friendly language (no "Stage NN" references).

**10.4 — pyproject + frontend package.json version bump**
- `backend/pyproject.toml` → `1.9.0`
- `frontend/package.json` → `1.9.0`

**10.5 — End-to-end smoke**
- The existing `tests/e2e/test_release_smoke_stage16.py` is renamed `test_release_smoke.py` (drop "stage" suffix) and extended to cover the new endpoints.
- Manual checklist appended to `STATE.md` Final.

**Stage 10 deliverable:**
- Zip: `auditarr-1_9_0-stage10.zip` (this is the release artifact)
- `STATE.md` final: lists known limits and any deferred items.

---

## 4. Per-stage executor checklist (the thing every executor must follow)

When the executor starts a stage, before writing any code, it must:

1. Unzip the previous stage's deliverable into a working directory.
2. `view` the previous `STATE.md` end-to-end.
3. `view` this `PLAN.md` for the stage it's executing.
4. `bash_tool` run: `pytest backend/tests -q` and `npm test --prefix frontend --silent` — confirm both green BEFORE any change.
5. Write code, test as it goes.
6. Run both test suites end-of-stage. If either fails, the executor does NOT ship a zip — it writes a `BLOCKED.md` explaining what failed and which file is implicated.
7. On green: zip the repo (excluding `node_modules`, `__pycache__`, `.pytest_cache`, `dist`, `build`, `*.pyc`), name `auditarr-1_9_0-stageN.zip`, and update `STATE.md`.
8. Call `present_files` with the zip and the updated `STATE.md`, plus one concise summary line.

---

## 5. STATE.md — the format every stage updates

Each stage's executor overwrites `STATE.md` with:

```
# Auditarr 1.9 — Implementation State

Last update: <ISO timestamp>
Last stage finished: <N> (<short title>)
Next stage: <N+1>

## Done
- <bullet per shipped item, with file paths>
- ...

## Skipped / Deferred (intentional)
- <item> — why, when revisit

## Failed / Blocked (must fix before next stage)
- <none, or list>

## Verification commands
$ pytest backend/tests -q
$ npm test --prefix frontend --silent

## Manifest (this stage)
- backend/app/...
- frontend/src/...
- tests/...
```

The executor of stage N+1 reads this and ONLY this for context.

---

## 6. What is explicitly out of scope for 1.9

To keep the scope honest:

- No new authentication providers (SSO/OIDC stays deferred).
- No multi-tenancy (single-operator install remains the contract).
- No mobile-first redesign — responsive but desktop-primary.
- No cloud-hosted Auditarr offering.
- No automated bulk transcoding without operator approval — the AI suggests, the operator decides.

---

## 7. Final notes for the executor

- When in doubt about a UI affordance, look at how the same kind of thing is built elsewhere in the codebase first. The codebase is consistent — match it.
- When in doubt about backend layering: `api → service → repository → model`. New behavior goes in `service`. Routers stay thin.
- Write the test before the code where it's practical. The user has been explicit about testing.
- Don't reformat unrelated files. Tools like ruff/eslint will run; only touch what the stage requires.
- When user-supplied scripts (`tdarr.txt`, `plex.txt`) inform the design, copy the **logic**, not the source. The supplied scripts are reference; the codebase has its own client, error model, and DTOs — those win.

End of plan.
