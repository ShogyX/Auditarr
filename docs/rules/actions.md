---
id: rules/actions
title: Rule actions
category: rules
tags: [rules, actions, quarantine, delete]
summary: What a rule does when it matches — full vocabulary including the quarantine and delete actions.
help_context: [rules.actions, rules.editor.actions]
related: [rules/conditions, rules/severity]
---

# Rule actions

When a rule matches, every entry in its `actions` array runs. Actions
are applied in order but each is **independent** — a failure in one
does not abort the others. Across many rules matching the same file,
results are aggregated: tags accumulate (deduped); severity escalates
to the highest matched value; a `delete` action by any matched rule
removes the file (delete is one-way — once any rule deletes, the row
and file are gone). The `quarantine` aggregation is gone
along with the action itself.

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

### `quarantine` 

**The `quarantine` action no longer exists.** It was retired in
 (of the) along with the rest
of the quarantine workflow — "delete means delete."

If your rule used to quarantine, you have two paths now:

 * Tag the matched files (`add_tag`) and let the operator
 review the tagged set.
 * If you want the file gone, use a `delete` action with a
 descriptive `reason`. The audit log records every removal.

The 0015 migration rewrites stored `type: "quarantine"`
actions to `type: "delete"` automatically (the `reason` is
preserved), so existing rules don't break on upgrade — but the
behaviour change is significant: those rules now hard-delete
instead of flagging. Review your rule set after upgrading.

### `delete` 

Move the file to the trash directory and remove its index row.
**Unconditional** — retired the `confirm`
flag that used to gate hard delete vs. soft delete (the soft
path was quarantine, which is also gone).

```json
{ "type": "delete", "reason": "Plex incompatible codec" }
```

`reason` is optional but **strongly recommended** — it lands
verbatim in the `file.deleted` audit-log entry that the rule
service writes for every successful delete. Operators reading
the audit trail see WHY a file was removed, not just WHEN.

When a `delete` action matches:

1. The file is moved (`shutil.move`) to `data_dir/trash/{id}__{name}`.
 The numeric id prefix prevents same-name collisions across libraries.
2. An audit-log entry is written (`action: "file.deleted"`,
 `actor_label: "rules"`, `metadata: { path, reason, trash_path }`).
3. The `MediaFile` row is removed from the database.
4. `media.deleted` is emitted on the event bus with the reason.

**Filesystem failures are non-fatal.** If the move fails
(permission, disk full, target conflict), the failure is logged
at `rules.hard_delete.failed` and the row is **preserved**. You
never lose both the file and its index entry in the same
operation.

**Audit-log failures are non-fatal at the file level** — if the
audit write fails for some reason, the file has already moved to
trash and the row gets removed regardless. The audit failure is
logged loudly at `rules.hard_delete.audit_failed` so an operator
notices the gap.

The trash directory accumulates files; emptying it is the
operator's responsibility — will surface a UI affordance.

## Visual rule builder

The visual builder at `/rules/{id}/edit` renders every action type
the backend vocabulary publishes, so any new action added in a
future stage shows up automatically without any frontend change.

For the `delete` action specifically, the builder renders the
optional `reason` as a labeled text input. retired the
old `confirm` checkbox — the hard-delete semantics no longer
have a gating flag to expose.

## Notes for plugin authors

The `Action` schema is a discriminated Pydantic union — new action
types are added by appending a model with a unique `type` literal and
threading it through the evaluator's `_apply_action` dispatch and the
rules service's persist step. See `app/rules/schema.py` and
`app/rules/evaluator.py` for the existing pattern.
