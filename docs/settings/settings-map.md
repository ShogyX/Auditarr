# Settings map

The canonical home for every operator-facing setting in Auditarr.
This is a flat directory — if you can't remember where a setting
lives, search this file first.

Two kinds of settings:

- **Process settings** (`AUDITARR_*` env vars + the `.env` file).
 These are read once at process boot via
 `app.core.settings.Settings`. Changing them requires a restart.
 They live in `.env`, the Docker compose file, or the systemd
 unit file — not the UI.

- **Runtime settings** (rows in `runtime_setting_overrides`).
 These are read on every request through the runtime-settings
 service and editable from the UI without a restart. Defaults
 live in `app.core.runtime_settings_schema`; the UI surface is
 on **Settings → System → Runtime**.

The "home" column below tells the operator exactly which UI page
hosts the editor.

---

## Application core

| Setting | Kind | Home | Notes |
| -------------------------------------- | -------- | ------------------------------------------------------- | --------------------------------------------------------------------------- |
| `AUDITARR_ENV` | Process | env / `.env` | `dev` / `staging` / `production`. Affects logging and CORS defaults. |
| `AUDITARR_SECRET_KEY` | Process | env / `.env` | Used to sign access / refresh tokens. Minimum 16 chars. **Don't change** after first install or every session is invalidated. |
| `AUDITARR_DATABASE_URL` | Process | env / `.env` | Postgres in production, SQLite for tests / single-user installs. |
| `AUDITARR_REDIS_URL` | Process | env / `.env` | Used by the arq worker queue and the rate limiter. |
| `AUDITARR_DATA_DIR` | Process | env / `.env` | Root for `trash/`, plugin install dir, and other persistent on-disk state. |
| `AUDITARR_API_ROOT` | Process | env / `.env` | URL prefix exposed in `system.info`. |
| `AUDITARR_APP_VERSION` | Process | env / `.env` | Override the image-stamped version. Default reads from `pyproject.toml`. |
| `AUDITARR_PLUGIN_DIR` | Process | env / `.env` | Where plugin manifests are installed. |

## Updater

| Setting | Kind | Home | Notes |
| ------------------------------------------------ | -------- | ------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `AUDITARR_UPDATE_FEED_URL` | Process | env / `.env` | Where the updater pulls release metadata. Default = project's GitHub Releases. |
| `AUDITARR_UPDATE_CHECK_INTERVAL_MINUTES` | Process | env / `.env` | Cron-tick interval for the feed check. |
| `AUDITARR_UPDATE_APPLY_SENTINEL` | Process | env / `.env` | Inside-container path where the apply-request file is written for the host helper to pick up. |
| `AUDITARR_UPDATE_APPLY_STATUS_PATH` | Process | env / `.env` | Inside-container path the host helper writes apply status to. |
| `AUDITARR_UPDATE_INSTALL_MODE` | Process | env / `.env` | `auto` / `docker` / `bare-metal` / `unmanaged`. Drives the UI's apply-button copy + the apply gate. |
| `AUDITARR_UPDATE_APPLY_TIMEOUT_SECONDS` | Process | env / `.env` | v1.9 — after this long in `requested`/`running`, an apply row is force-marked `failed` by the reaper. |
| — | UI | Help & updates → Updates card | Check for updates · Apply · Roll back · Force-clear (visible on stuck applies > 5 min). |

## Libraries

| Setting | Kind | Home | Notes |
| ------------------------ | ------- | --------------------------------------------- | ------------------------------------------------------------------------------ |
| Library name / root path | Runtime | Settings → Workspace → Libraries card | Add / edit / delete via the LibraryEditDialog. |
| Scan interval | Runtime | Settings → Workspace → Libraries card | Per-library scan cadence in minutes; 0 disables the automatic schedule. |
| Library enabled | Runtime | Settings → Workspace → Libraries card | Disabled libraries are excluded from `POST /scans/all` and the next dashboard. |

## Appearance

| Setting | Kind | Home | Notes |
| -------------- | ------- | ------------------------------------------------- | -------------------------------------------------------------------- |
| Theme | Runtime | Settings → Workspace → Appearance card / sidebar | `light` / `dark`. Toggle from the avatar menu also works. |
| Accent | Runtime | Settings → Workspace → Appearance card | Eight token-driven accents. |
| Sidebar layout | Runtime | Settings → Workspace → Appearance card | `sidebar` (default) / `top` nav layout. |

## Integrations & path mappings

| Setting | Kind | Home | Notes |
| ------------------------------- | -------- | ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Integration list (Plex, Sonarr…) | Runtime | **Integrations page** | Add, edit, enable / disable, run a healthcheck, sync tags, generate webhook secret. |
| VirusTotal config | Runtime | **Integrations → VirusTotal card** | API key (secret), enable / disable, quota windows. v1.9 deferral — some VT runtime settings still live on Settings → System → Runtime; consolidation moves to a later stage. |
| Per-integration path mappings | Runtime | **Integrations page → Path mappings panel** | v1.9 — was on Settings → Integrations sub-tab; moved here. Surfaces every integration's `config.path_mappings` and the global mapping layer in one editor. |
| Global path mappings | Runtime | **Integrations page → Path mappings panel** | Cross-integration fallback when no integration-scoped mapping matches. |
| Webhook source allowlist | Runtime | Settings → System → Runtime | Comma-separated host/IP allowlist for inbound webhook calls. |

## Rules engine

| Setting | Kind | Home | Notes |
| ----------------------------- | ------- | -------------------------------------------- | -------------------------------------------------------------------------------------- |
| Rule definitions (DSL JSON) | Runtime | Rules page | Visual rule builder + raw JSON view. Built-in rules + operator-authored rules in one table. |
| Rule priorities | Runtime | Rules page → Rule editor | Per-rule priority used when multiple rules match. |
| Notification throttle windows | Runtime | Rules page → Rule editor → Notify action | Per-rule throttle ID, ttl, and window count. |

## Optimization

| Setting | Kind | Home | Notes |
| ---------------------- | ------- | ---------------------------------------------- | ------------------------------------------------------------------------------ |
| Optimization profiles | Runtime | Optimization → Profiles card | Bitrate / codec / container targets per profile. Default routing target. |
| Routing target | Runtime | Profile editor | `in_process` (Auditarr's own worker) or `provider` (Tdarr / external). |
| Queue paused/resumed | Runtime | Optimization → Queue card | Pause halts the in-process worker; queued items resume on unpause. |

## Notifications

| Setting | Kind | Home | Notes |
| -------------------- | ------- | ------------------------------------------------- | ------------------------------------------------------------------------------ |
| Notification channels | Runtime | Notifications → Channels card | Email / Slack / webhook channel definitions and their credentials. |
| Per-rule channel binding | Runtime | Rules page → Rule editor → Notify action | Which channel a matched rule routes to. |

## Security

| Setting | Kind | Home | Notes |
| -------------------- | -------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Account profile | Runtime | Settings → Security → Account security / `/account` | Name, email, password change. |
| Active sessions | Runtime | Settings → Security → Account security / `/account` | "Sign out everywhere" revokes refresh sessions; the current session is preserved. |
| Audit log | Runtime | Settings → Security → Audit log entry → `/settings/audit` | Filterable + paginated. Captures every login, configuration change, admin action, factory reset, etc. |

## System

| Setting | Kind | Home | Notes |
| ----------------------------- | -------- | ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Runtime settings (general) | Runtime | Settings → System → Runtime | The catch-all for runtime knobs that don't have a dedicated home elsewhere on this map. Editing here is admin-only. |
| Encrypted secrets | Runtime | Settings → System → Secrets | Per-integration API keys, webhook signing secrets, plugin credentials. Stored AES-encrypted with the app's secret key as the KEK. |
| System config (read-only) | Process | Settings → System → System config | Read-only view of process settings (env vars). Useful to confirm what the running container actually loaded. |
| Housekeeping cadence | Runtime | Settings → System → Housekeeping | TTL configuration for notification deliveries, update checks, rule evaluations, job runs. The arq worker prunes per these knobs. |
| Reload docs index | Process | Settings → System → Housekeeping → SystemMaintenanceCard | Admin button — re-walks `docs/*.md` and rebuilds the in-process search index without restarting the server. |
| **Factory reset** | Process | Settings → System → Housekeeping → SystemMaintenanceCard → Danger zone | v1.9 — wipes every table except `users` / `audit_log` / `alembic_version`, purges `data_dir/trash/`. Two-gate confirmation: `<details>` to reveal, then a typed-phrase modal. The operator's session is preserved. |

## Dashboard

| Setting | Kind | Home | Notes |
| ----------------------------- | ------- | --------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Card visibility (per card) | UI prefs | Dashboard → each card's hamburger menu | v1.9 — every card now honours the same `useDashboardCardDisabled` hook. Pre-1.9 some cards were buggy and ignored the setting. |
| Card order | UI prefs | Dashboard → reorder via drag | Persisted in the local UI prefs store. |
| Collapsed / expanded section | UI prefs | Dashboard → each card's chevron | Hidden cards stay in the active grid; disabled cards move to the rail. |

---

## How to find the home of a setting that isn't on this map

1. **Process setting?** Grep `app/core/settings.py` for the field
 name. The `AUDITARR_…` env-var prefix maps 1:1 to the Settings
 class attribute name (uppercased + prefixed).
2. **Runtime setting?** Grep `app/core/runtime_settings_schema.py`
 for the field name. The UI surface is rendered from that
 schema by `RuntimeSettingsPanel`; the canonical home is
 **Settings → System → Runtime** unless a dedicated card on
 another page exposes it (e.g. VirusTotal settings on the
 Integrations page).
3. **Per-integration knob?** Look at the integration's row on
 `/integrations` — the IntegrationConnectDialog hosts every
 per-integration setting through the schema-driven inputs.

If you find a setting that isn't on this map, please add a row to
the table above.
