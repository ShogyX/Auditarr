---
id: rules/actions
title: Rule actions
category: rules
tags: [rules, actions, quarantine, delete]
summary: What a rule does when it matches — full vocabulary including the Stage 9 quarantine and delete actions.
help_context: [rules.actions, rules.editor.actions]
related: [rules/conditions, rules/severity]
---

# Rule actions

When a rule matches, every entry in its `actions` array runs. Actions
are applied in order but each is **independent** — a failure in one
does not abort the others. Across many rules matching the same file,
results are aggregated: tags accumulate (deduped); severity escalates
to the highest matched value; quarantine is a one-way switch (once
any rule quarantines, the file stays quarantined).

The full vocabulary is published by the backend at
`GET /api/v1/rules/vocabulary` and rendered by the visual rule
builder — you don't have to memorize it.

## Action types

### `set_severity`

Set the file's severity to a fixed level. Severity escalation across
rules is monotonic: if one rule sets `warn` and another sets `high`,
the file ends up at `high`.

```json
{ "type": "set_severity", "severity": "warn" }
```

Valid values: `ok`, `info`, `warn`, `high`, `error`, `crit`.

### `add_tag`

Add a tag to the file in Auditarr's index. Tags are de-duped across
rules so the same tag from two rules doesn't create two rows.

```json
{ "type": "add_tag", "tag": "needs-review" }
```

### `queue_optimization`

Queue an optimization job for the file using the named profile.

```json
{ "type": "queue_optimization", "profile": "h265-medium" }
```

The profile must exist (configured under Optimization → Profiles).

### `notify`

Send a notification through the configured providers, filtered by
the active channel.

```json
{
  "type": "notify",
  "channel": "discord-ops",
  "message": "Codec mismatch: {path}"
}
```

The `channel` is required; `message` is optional (defaults to a
generic format string derived from the rule name).

### `quarantine` <span class="pill">Stage 9</span>

Flag the file as quarantined. Emits `media.quarantined` for any
listening consumer. Quarantined files are hidden from the default
files view but remain in the index.

```json
{ "type": "quarantine", "reason": "Unwanted codec" }
```

`reason` is optional but **highly recommended** — it persists on
the file row so an operator scanning the quarantine list later can
tell which rule caught it. If multiple rules quarantine the same
file, the first non-null reason wins.

### `delete` <span class="pill">Stage 9</span>

Move the file to the trash directory and remove its index row.

```json
{ "type": "delete", "confirm": true }
```

**The `confirm` flag is intentional defensiveness.** Without it
(`confirm` omitted or false), the action falls back to a
**soft delete**: the file is quarantined and flagged for review, but
neither the file nor its row is removed. Only `confirm: true`
triggers the destructive path.

When `confirm: true`:

1. The file is moved (`shutil.move`) to `data_dir/trash/{id}__{name}`.
   The numeric id prefix prevents same-name collisions across libraries.
2. The `MediaFile` row is removed from the database.
3. `media.deleted` is emitted.

**Filesystem failures are non-fatal.** If the move fails (permission,
disk full, target conflict), the failure is logged at `rules.hard_delete.failed`
and the row is **preserved**. You never lose both the file and its
index entry in the same operation.

The trash directory accumulates files; emptying it is the operator's
responsibility — Stage 14 will surface a UI affordance.

## Visual rule builder

The visual builder at `/rules/{id}/edit` renders every action type
the backend vocabulary publishes, so any new action added in a
future stage shows up automatically without any frontend change.

For the `delete` action specifically, the builder renders `confirm`
as a labeled checkbox with the hint text visible inline — the
hard-delete semantics is too consequential to bury in a placeholder.

## Notes for plugin authors

The `Action` schema is a discriminated Pydantic union — new action
types are added by appending a model with a unique `type` literal and
threading it through the evaluator's `_apply_action` dispatch and the
rules service's persist step. See `app/rules/schema.py` and
`app/rules/evaluator.py` for the existing pattern.
