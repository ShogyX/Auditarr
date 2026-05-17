---
id: getting-started/upgrade-to-v1.7
title: Upgrading to Auditarr v1.7
category: getting-started
tags: [upgrade, v1.7, migration, quarantine]
summary: What changes when you upgrade an existing Auditarr install to v1.7, and what to do about it before you run the migration.
help_context: [getting-started.upgrade]
related: [getting-started/installation, rules/actions]
---

# Upgrading to Auditarr v1.7

v1.7 ships **fifteen migrations** on top of the v1.6.x line. Every
migration is non-destructive **except one** — the quarantine
removal in Stage 05 — and the regular upgrade flow handles them
automatically. The notes below cover the things you should know
about *before* you run the upgrade.

## The one destructive change: quarantine is gone

Auditarr v1.6 had a "quarantine" surface: files an operator
flagged as suspicious sat in a `quarantined` column on the
`media_files` table and were hidden from the main views. v1.7
removes the feature entirely — the column is gone and the rule
action is deleted from the engine. Operators told us the surface
was confusing (it overlapped with both "low severity" and "rule
flagged"), so we removed it rather than try to clean it up.

**What this means for you.**

- The migration **deletes every `media_files` row that had
  `quarantined = TRUE`**. If you had quarantined files, they
  will be removed from the database when you upgrade. The
  files themselves on disk are NOT touched — Auditarr never
  modifies files on disk during a migration — but the records
  pointing to them disappear.
- The next scan will re-index those files normally, into
  whatever category and severity their probed metadata earns.
  Anything that *should* be flagged on the new system will be
  flagged via the regular rule engine (Stage 06 rule engine
  with severity, action, conditions; see
  [rules/conditions](../rules/conditions)).
- The migration **logs at WARNING level** how many rows it
  deleted, so you'll see something like
  `WARNING: 47 quarantined media_files rows deleted as part
  of quarantine-removal migration` in your application log.
  This is normal.

**Before you upgrade — if you care about your quarantine list.**

Before running the v1.7 upgrade, export the quarantine list from
the Files page so you have a record of what was flagged. The
export is plain CSV; we'll re-index those files on the next
scan, but if you used the quarantine column as a "shame list"
of titles you'd dispositioned, save the CSV before upgrading.

## Other migrations (non-destructive)

The other fourteen migrations are purely additive and don't
require any operator action:

- **Stage 01** — installer renames (`install.sh` →
  `install-docker.sh`; `install-bare-metal.sh` is the new
  bare-metal entry). The old `install.sh` is a stub that
  prints the new name and exits.
- **Stage 02–03** — column-resize state for the Files and
  Rules tables, and new built-in Plex compatibility rule
  (see [rules/plex-compatibility](../rules/plex-compatibility)
  for the honest caveats).
- **Stage 06** — new rule engine schema (`severity`,
  `action`, `conditions` columns on `rules`). The migration
  preserves your existing rules unchanged; new columns
  default to safe values.
- **Stage 07–08** — optimization profile changes
  (`routing_target` column with `in_process` default;
  `provider_profile_id` opaque pointer). Existing profiles
  default to `in_process`, which matches pre-v1.7 behaviour.
- **Stage 09** — live-playback subsystem reads playback
  events from your media servers; opt-in via the new
  Live now dashboard card.
- **Stage 10** — VT integration (new `vt_status` column on
  `media_files`, new `vt_queue` table). Disabled by default;
  enable in Settings → Integrations.
- **Stage 11** — webhook HMAC bypass + IP/DNS source
  whitelist columns. Existing webhooks behave unchanged.
- **Stage 12** — `users.must_change_password` and
  `password_reset_tokens.must_change_on_use` columns. Both
  default to FALSE; existing accounts are unaffected.
- **Stage 13** — no schema changes. Frontend-only: dashboard
  card management, scan-progress-survives-navigation,
  invalidation audit.

## Running the upgrade

The standard upgrade flow runs every migration in sequence:

```bash
# Docker install
./install-docker.sh upgrade

# Bare-metal install
./install-bare-metal.sh upgrade
```

The installer:

1. Takes a database snapshot to a timestamped path under
   `/var/lib/auditarr/backups/` (Docker) or `~/.auditarr/backups/`
   (bare metal). If anything goes wrong you can restore from
   the snapshot.
2. Stops the running services.
3. Pulls the new image / extracts the new tarball.
4. Runs the alembic chain to bring the schema up to
   `0026_stage12_must_change_pw` (the v1.7 head).
5. Restarts the services.

The full migration chain takes well under a minute on the
sizes most operators run (≤ 1M files). If something is off,
the installer leaves the snapshot in place and the previous
image / tarball still installed under a parallel path — so a
rollback is a service-stop, swap-symlink, service-start away.

## Library mount reminder (Docker)

If you're upgrading a Docker install, double-check that your
`docker-compose.yml` mounts your library directories into the
container — Auditarr can't scan files it doesn't have a
filesystem view of. The convention is `volumes:` entries
mapping each host library directory to the same path inside
the container, e.g.:

```yaml
volumes:
  - /mnt/media/movies:/mnt/media/movies:ro
  - /mnt/media/tv:/mnt/media/tv:ro
```

Read-only mounts (`:ro`) are recommended for everything except
directories you've explicitly given Auditarr permission to
modify (rule actions, optimization output paths).

## Rolling back

If you decide v1.7 isn't for you, the alembic chain is
reversible **except for the Stage 05 quarantine column drop**.
Restoring that requires the pre-upgrade snapshot.

The downgrades for Stages 01–04 and 06–13 are clean — they
either drop a column or rewrite a row format that was always
optional.

## Where to ask for help

- Container logs: `docker compose logs -f auditarr` (Docker)
  or `journalctl -u auditarr -f` (bare metal, systemd).
- The Help page in-app: full-text search across this docs
  tree, including this upgrade page.
- The community forum and GitHub issues — link in the app's
  footer.
