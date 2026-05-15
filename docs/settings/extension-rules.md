---
id: settings/extension-rules
title: Extension rules
category: settings
tags: [settings, extensions, scanner, rules, quarantine]
summary: Per-extension scanner + rule-engine overrides — ignore, accept, flag as malicious, or index-only.
help_context: [settings.extension-rules, system.extension-rules]
related: [rules/actions, rules/severity]
---

# Extension rules

Extension rules override the scanner's default classification and
the rule-engine's severity policy on a per-extension basis. They're
the right tool when you want **policy by file type** independent of
content matching.

The rules live at `/api/v1/system/extension-rules` and are admin-managed.
The UI panel for them ships in a later stage (Stage 14); until then,
manage them via the API — `curl`, an OpenAPI client, or the
auto-generated `/docs` Swagger page all work.

## Dispositions

Every rule has a single `disposition` chosen from four values:

### `ignore`

The file is **skipped entirely** during scan. It's not indexed, not
probed, not orphan-tracked. The scanner's enumeration step still
walks the path (you can't skip directory traversal), but the loop
body short-circuits before any DB or ffprobe work.

Use for: build artifacts, lock files, `.DS_Store`, etc.

### `stats_only`

The file is indexed at severity `info` and the rule engine still
re-evaluates it on the next pass, but the explicit `info` sets a
soft floor — the row won't escalate to `warn`/`high` via the
rule pipeline. Useful when you want a file type visible in dashboard
counts but never alerting.

Use for: `.nfo` files, `.txt` notes, manifest files alongside media.

### `malicious`

The file is indexed at severity `crit` AND quarantined immediately.
The scanner stamps `quarantined_reason` with `"Extension rule: {ext}
marked malicious"`. This bypasses the rule engine's normal
escalation — operators see the file flagged from the moment of scan.

Use for: known-dangerous extensions on systems where executable
content shouldn't appear (`.exe`, `.scr`, etc.).

### `accepted`

The file is indexed at severity `ok` and the rule engine cannot
escalate it. Use this when a file type is legitimate in your library
and you want it absolutely silent regardless of what other rules
match.

Use for: subtitle sidecar files, intentional thumbnails, etc.

## Storage shape

```json
{
  "id": "uuid",
  "extension": "exe",
  "disposition": "malicious",
  "enabled": true
}
```

Extensions are stored **lower-cased and without the leading dot**.
The API accepts `".MP4"`, `"mp4"`, or `"MP4"` and normalizes to
`"mp4"`. The unique constraint is on the canonical form, so you
can't create two rules for the same extension under different
spellings.

## API reference

```
GET    /api/v1/system/extension-rules         # list
POST   /api/v1/system/extension-rules         # create (admin)
PATCH  /api/v1/system/extension-rules/{id}    # update (admin)
DELETE /api/v1/system/extension-rules/{id}    # delete (admin)
```

`POST` with an extension that already exists returns `409` with
`details.existing_rule_id` so you can find the conflicting row.
`PATCH` enforces the same uniqueness when changing `extension`.

## Performance

The scanner loads the disposition map **once per scan** via
`MediaExtensionRuleRepository.load_disposition_map()` → a tiny dict.
Per-file lookup is O(1). Even with hundreds of extension rules and
hundreds of thousands of files, the cost is negligible.

## See also

- [Rule actions](/help/rules/actions) — the `quarantine` action
  reaches the same end-state as a `malicious` disposition.
- [Severity levels](/help/rules/severity) — what `info`, `crit`,
  etc. mean for the rest of the pipeline.
