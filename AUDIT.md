# Auditarr 1.9 — Security & Bug Hunt

Audit pass over v1.9 (Stages 1-9). Findings categorized SEC
(security), BUG (functional), ROBUST (DoS/resource), DATA
(integrity), UX (operator-visible correctness).

Severity: HIGH | MEDIUM | LOW.

## Findings — Claude's pass

### LOG-1 (BUG, MEDIUM)
**Location**: `backend/app/api/v1/system.py:445-455`, `:517-527`
**Issue**: `since` query parameter parsed via `datetime.fromisoformat`
can return tz-naive if operator omits timezone suffix
(`?since=2026-05-18T08:00:00`). Comparing tz-naive `since_dt` to
tz-aware record timestamps raises `TypeError`, 500'ing the endpoint.
**Fix**: If parsed `since_dt` is tz-naive, assume UTC before compare.

### LOG-2 (BUG, LOW)
**Location**: `backend/app/api/v1/system.py:463`
**Issue**: `int(cursor or 0)` accepts negatives — slices from end.
**Fix**: Clamp to `max(0, int(cursor or 0))`.

### LOG-3 (UX, LOW)
**Location**: `backend/app/api/v1/system.py:474`
**Issue**: `last_error_at` is the buffer-wide last error, not
filtered by request. Operator filtering "show API logs" still sees
the error pill for an error in the worker category.
**Fix**: Compute `last_error_at` from the filtered records.

### LOG-4 (MAINTENANCE, LOW)
**Location**: `backend/app/api/v1/system.py:437-455`, `:509-527`
**Issue**: Filter pipeline duplicated across `list_logs` and
`export_logs`. Diverges easily.
**Fix**: Extract `_apply_log_filters` helper.

### LOG-5 (SEC, LOW)
**Location**: `backend/app/core/log_buffer.py:54`
**Issue**: `media_file_path` allow-listed; exposes filesystem
layout to log readers. Admins only, but loose-coupling risk.
**Fix**: Defer.

### LOG-6 (SEC, HIGH)
**Location**: `frontend/src/hooks/useLogs.ts:81`
**Issue**: `downloadLogsNdjson` sets `window.location.href` —
strips the Bearer header, endpoint 401s, operator gets nothing.
**Fix**: Use `fetch()` with bearer, blob the response, click a
hidden anchor with `download=`.

### DEV-1 (BUG, MEDIUM)
**Location**: `backend/app/services/playback/poller.py:534-539`
**Issue**: Dead "refresh name" code can never execute — the
`client_key` hash includes `name`, so renames produce a new row,
not a row with a stale name.
**Fix**: Remove the dead refresh code, document the
rename-as-new-row design.

### DEV-2 (BUG, MEDIUM)
**Location**: `backend/app/services/playback/poller.py:511-532`
**Issue**: `_upsert_device` runs OUTSIDE the event-insert savepoint.
If it raises `IntegrityError` (concurrent race) the parent
transaction is invalid and subsequent operations fail.
**Fix**: Wrap device upsert in its own `begin_nested` savepoint.

### DEV-3 (BUG, LOW)
**Location**: `backend/app/services/playback/poller.py:511-518`
**Issue**: Select-then-insert race. Not reachable today (manager
serializes pollers per integration) but latent.
**Fix**: Defer; couple with DEV-2.

### DEV-4 (DATA, LOW)
**Location**: `backend/app/services/playback/poller.py:814`
**Issue**: `device_name` not trimmed before hashing — leading/
trailing whitespace creates duplicate device rows.
**Fix**: `.strip()` before hashing.

### DEV-5 (SEC, LOW)
**Location**: `backend/app/api/v1/playback.py:463`
**Issue**: `CurrentUser` not `AdminUser` — any user sees devices.
**Fix**: Defer (matches existing playback endpoint precedent).

### AI-1 (BUG, MEDIUM)
**Location**: `backend/app/services/ai/suggestions.py:143-147`
**Issue**: `provider_kind` defaults to `"openai"` silently.
Misconfigured Ollama integration would silently call OpenAI's wire
shape.
**Fix**: Fail fast — `error="provider_kind is required"`.

### AI-3 (BUG, HIGH)
**Location**: `backend/app/services/ai/suggestions.py:243`
**Issue**: `dedup_key=f"ai:{kind}:{name}"`. RuleSuggestion's
`dedup_key` is unique. Two calls proposing overlapping names
crash the commit. Re-running `generate()` collides with prior
pending suggestions guaranteed.
**Fix**: Hash the definition into the dedup_key; check
`get_by_dedup_key` before insert.

### AI-4 (SEC, MEDIUM)
**Location**: `backend/app/services/ai/suggestions.py:213-225`
**Issue**: Soft constraint only — system prompt says "never propose
delete" but `RuleDefinition` schema accepts `delete` actions. A
hallucinating or jailbroken LLM could emit one.
**Fix**: Hard-reject proposals with `delete` actions in the persist
loop.

### AI-5 (SEC, LOW)
**Location**: `backend/app/services/ai/suggestions.py:196-199`
**Issue**: Provider exception text → audit log unchanged. Low
likelihood of api_key leak, but defensive sanitization is cheap.
**Fix**: Strip `Bearer ...` / `sk-...` patterns before persisting.

### AI-6 (ROBUST, LOW)
**Location**: `backend/app/services/ai/suggestions.py:156`
**Issue**: Budget check is read-modify-write race.
**Fix**: Defer (admin-only).

### AI-7 (ROBUST, LOW)
**Location**: `backend/app/services/ai/suggestions.py:176`
**Issue**: Context payload size unbounded.
**Fix**: Defer (provider returns 400 with clear error).

### AI-8 (UX, LOW)
**Location**: `backend/app/services/ai/suggestions.py:366`
**Issue**: All libraries anonymize to same `<library>` placeholder.
**Fix**: Use `<library:NAME>` per library.

### AI-10 (DATA, LOW)
**Location**: `backend/app/services/ai/suggestions.py:235-244`
**Issue**: Raw dict persisted instead of validated model's
`model_dump()`. Brittle if `RuleDefinition` ever applies normalization.
**Fix**: Persist `parsed.model_dump()`.

### STALE-1 (ROBUST, MEDIUM)
**Location**: `backend/app/services/playback/stale_rule_analyzer.py:167-174`
**Issue**: O(N*M) — full playback events loaded per rule. For 50
rules and 10k events, 500k row materializations per analyzer run.
**Fix**: Compute the direct-play ratio ONCE outside the per-rule
loop.

### STALE-2 (UX, LOW)
**Location**: `backend/app/services/playback/stale_rule_analyzer.py:148-161`
**Issue**: Rule-agnostic — flags all firing rules identically when
global direct-play ratio is high.
**Fix**: Defer — better fix needs per-file rule-match tracking.

---

## Operator-supplied findings

### OP-1 (UX, MEDIUM) — Rules page layout cramped
**Location**: `frontend/src/features/rules/RulesPage.tsx`
**Issue**: The two-column Rules + Templates layout uses only half
the screen; both boxes stack narrowly with scrolling required to
see everything.
**Fix**: Use full viewport width; expand the two cards to fill
available space.

### OP-2 (BUG, HIGH) — Rule conditions cannot nest AND/OR
**Location**: `frontend/src/features/rules/RuleEditor*`, `backend/app/rules/schema.py`
**Issue**: Rule editor toggles ALL conditions between AND/OR globally.
Operators need mixable logic: `(A AND B) OR (C AND D)`. The DSL
schema already supports `all`/`any`/`not` composites; the editor
flattens them.
**Fix**: Add a tree editor surface that supports nested groups.

### OP-3 (UX, MEDIUM) — Rule evaluation panel cramped
**Location**: `frontend/src/features/rules/RuleEvaluator*` or similar
**Issue**: Text in the evaluation panel is squished.
**Fix**: Increase column / panel width; allow text wrapping.

### OP-4 (UX, MEDIUM) — Disabled rules indistinguishable; no built-in/custom tag
**Location**: `frontend/src/features/rules/RulesTable.tsx`
**Issue**:
  1. Disabled rules render identically to enabled ones; should be
     visually shadowed.
  2. No badge distinguishes built-in templates vs operator-authored
     custom rules.
**Fix**: Reduce opacity (or apply muted styling) to disabled rule
rows; render a "Built-in" or "Custom" pill on each rule.

### OP-5 (UX, MEDIUM) — Frontend-wide space utilization audit
**Location**: All pages, particularly `IntegrationsPage`
**Issue**: Pages constrained to ~half viewport width; everything
stacks vertically as narrow boxes requiring scroll. Wide screens
go unused.
**Fix**: Widen container max-widths; convert vertical stacks to
multi-column grids on wide viewports (≥xl breakpoint).

### OP-6 (BUG, MEDIUM) — Templates tab missing built-ins
**Location**: `frontend/src/features/rules/RulesPage.tsx` (templates tab)
**Issue**: Built-in rule templates aren't appearing in the
templates tab.
**Fix**: Investigate — Stage 4.4 seeded built-in templates; either
the seed isn't running, the read query is filtered wrong, or the
frontend isn't displaying the seeded rows.

### OP-7 (UX, MEDIUM) — Categories card stats not actionable
**Location**: `frontend/src/features/dashboard/CategoriesCard.tsx`
**Issue**:
  1. Resolution/language/container stats are display-only — clicking
     a row should deep-link to the Files page filtered to that bucket.
  2. Bitrate shown in kbps only — add Mbps too.
  3. Median bitrate breakdown needs better surfacing (sortable, clearer
     median-vs-mean explanation).
**Fix**:
  1. Each stat row becomes a link to `/files?<filter>`.
  2. Surface Mbps alongside kbps for human readability.
  3. Add sortable median bitrate column.

### OP-8 (BUG, MEDIUM) — Foreign-language-without-subs not highlighted
**Location**: Backend categories analyzer + frontend
**Issue**: Operator-configurable "preferred audio language" check
is missing. Foreign-language media without subtitles in a desired
language should surface.
**Fix**: Add `preferred_audio_languages` (list) + `preferred_subtitle_languages`
(list) to settings. Surface a count of "foreign audio without
preferred subs" on the dashboard.

### OP-9 (UX, MEDIUM) — No surface for "incompatible media" count
**Location**: Dashboard / Categories
**Issue**: Rule-driven "this file is incompatible" findings aren't
totalled anywhere.
**Fix**: Add a count of media flagged by audio/video compat rules
on the dashboard; clickable to filtered Files.

### OP-10 (BUG, HIGH) — Plex live playback not appearing
**Location**: `backend/plugins/plex/backend.py` or
`backend/app/services/playback/poller.py`
**Issue**: Plex integration neither surfaces live playback nor
ingests historical playback. Operator sees empty playback data.
**Fix**: Investigate Plex provider — likely the endpoint URLs or
auth shape have shifted. May need to check actual HTTP responses
from a running Plex.

### OP-11 (BUG, HIGH) — Tracearr healthcheck 404
**Location**: `backend/plugins/tracearr/backend.py`
**Issue**: `"Tracearr /api/health returned HTTP 404"`. The endpoint
URL the plugin uses doesn't exist on the Tracearr service.
**Fix**: Look up the actual Tracearr endpoint (likely `/health` or
`/api/v1/health` rather than `/api/health`). Update the plugin.

### OP-12 (UX, HIGH) — Central log interface not surfaced
**Location**: `frontend/src/components/shell/*`
**Issue**: Stage 8.1 added `/system/logs` but no navigation entry
exposes it. Operators can't find the page.
**Fix**: Add a nav entry for the Logs page; the sidebar's "System"
section is the obvious home.

### OP-13 (BUG, HIGH) — Tdarr profile picker empty, optimization rejected
**Location**: `backend/plugins/tdarr/backend.py`,
`frontend/src/features/optimization/OptimizationProfileDialog.tsx`
**Issue**: `'tdarr' provider rejected job: Tdarr requires a
provider profile id (the Tdarr plugin name). Edit the Auditarr
profile and pick one from the plugin list.` — the plugin/profile
list isn't being shown in the profile editor.
**Fix**: Surface `list_transcode_profiles` results in the profile
editor's provider-profile dropdown. Likely the editor doesn't fetch
or render the discovered profile list.

### OP-14 (BUG, HIGH) — AI integration doesn't appear in directory
**Location**: Plugin discovery
**Issue**: `ai_provider` plugin was added (Stage 9.3) but doesn't
show in the integrations directory the operator browses.
**Fix**: Check plugin discovery — the manifest may not register
correctly, or the directory may filter out plugins that don't
implement certain capabilities.

### OP-15 (BUG, HIGH) — VT not triggering for existing media
**Location**: `backend/app/scanners/*`, `backend/plugins/virustotal/backend.py`
**Issue**: VirusTotal scan rule doesn't fire against existing media
files — only new ones (if any).
**Fix**: Trace the VT trigger path. Likely the rule-engine VT
action only runs on first-encounter rather than on rule
evaluation against the existing library. Add a "Run rule now"
button or fix the trigger.

---

## Resolution status

| ID       | Status   | Notes                              |
|----------|----------|------------------------------------|
| LOG-1    | pending  | tz-naive `since` comparison crash  |
| LOG-2    | pending  | negative cursor clamp              |
| LOG-3    | pending  | filtered last_error_at             |
| LOG-4    | pending  | helper extraction                  |
| LOG-5    | deferred | media_file_path allow-list (LOW)   |
| LOG-6    | pending  | export-button auth                 |
| DEV-1    | pending  | remove dead refresh code           |
| DEV-2    | pending  | savepoint around device upsert     |
| DEV-3    | deferred | race latent only                   |
| DEV-4    | pending  | trim name before hash              |
| DEV-5    | deferred | matches precedent                  |
| AI-1     | pending  | provider_kind fail-fast            |
| AI-3     | pending  | dedup_key + recheck                |
| AI-4     | pending  | hard-reject delete actions         |
| AI-5     | pending  | sanitize audit error               |
| AI-6     | deferred | admin-only                         |
| AI-7     | deferred | provider 400 already clear         |
| AI-8     | pending  | per-library placeholder            |
| AI-10    | pending  | persist model_dump()               |
| STALE-1  | pending  | factor ratio out of per-rule loop  |
| STALE-2  | deferred | needs per-file tracking            |
