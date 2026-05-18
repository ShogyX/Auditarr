---
id: files/delete
title: Deleting files
category: files
tags: [files, delete, trash]
summary: How direct deletion works, where files go, and how to recover.
help_context: [files.delete]
related: [files/overview, rules/actions]
---

# Deleting files

Auditarr can delete files from disk in three ways: a rule with a `delete` action, the bulk-delete affordance in the Files page, and the per-file delete in the file detail drawer. All three paths end the same way — the file is **moved to the trash directory under `data_dir/trash/`** and the database row is removed.

## What "delete" actually does

1. The file is moved (`os.rename`) into `<data_dir>/trash/<media_id>__<filename>`. The two-segment prefix prevents collisions when two libraries had a file with the same name.
2. The `media_files` row is deleted. Any `media_tags`, `rule_evaluations`, and `optimization_items` referencing it are cascaded (foreign keys are set up for this).
3. An audit log entry is written with `action=file.deleted`, the original path, and the reason (rule name, or `"manual"` for UI-driven deletes).

The trash directory is never automatically emptied. Operators can review and restore files manually, then sweep the directory on whatever cadence they like.

## Restoring a deleted file

The fastest path back is to `mv` the trashed file from `<data_dir>/trash/` back to its library and re-run the scan. The next scan re-indexes it as a fresh row with a fresh id; old rule-evaluation history is not restored.

## Disk space caveat

Because delete is a `mv` rather than a real remove, the file still occupies the trash directory on the same filesystem. If `data_dir` is on a different volume than the library root, the move falls back to copy-and-delete, which uses disk twice transiently.

## Rule-driven delete safety

Rules with a `delete` action must carry `acknowledged_destructive: true` in the rule definition. The visual editor surfaces this as a checkbox; the JSON tab requires it explicitly. The backend rejects rule saves that include `delete` without the flag, so an accidental destructive rule can't be authored through the API.
