---
id: files/overview
title: Files page
category: files
tags: [files, browse, filter, columns]
summary: Browse, filter, sort, and act on every file Auditarr has indexed.
help_context: [files.overview]
related: [rules/overview]
---

# Files page

The Files page is Auditarr's primary surface for **browsing the
indexed library**. Every file the scanner has walked appears here,
classified by category and decorated with the most recent
``ffprobe`` result.

## Quick orientation

The page has three sections, top to bottom:

1. **Scope bar** — the segmented control (`All` / `Media` /
 `Non-media`) and the severity chip row. Toggling severities
 filters which rows the table shows; toggling the scope narrows
 the chip row to the relevant subset.
2. **Toolbar** — search across the path, library / category
 dropdowns, codec / container filter popover, and the
 per-column-filter toggle plus the
 column-visibility menu. The quarantine-view
 dropdown was removed in along with the
 quarantine feature itself.
3. **Table** — sortable, resizable columns. Click a column edge
 and drag to resize; your widths persist across reloads. Click
 a header to sort.

## Columns

The default visible set is **File**, **Category**, **Severity**,
**Size**, **Codec**, **Resolution**, **Subs**. Use the
column-visibility menu (right end of the toolbar) to add
**Container**, **Updated**, **Ext**, **Rules**, or **Tags**. The
File column always stays visible — it's the anchor for
identifying a row.

## Severity meanings

Severity reflects the most-severe rule that fired against a row.
 v1.7 aligned the column pill colour to the scope-bar
swatch — both render from the same ``sev-*`` CSS variable.

| Severity | Meaning |
|---|---|
| `ok` | No rules fired — file looks healthy. |
| `info` | Rule-tagged but no operator action needed. |
| `warn` | Worth a look; not urgent. |
| `high` | Operator should investigate. |
| `error` | Likely broken file (corrupt, unreadable). |
| `crit` | Almost certainly a problem; e.g. executable in library, VirusTotal-flagged file. |

## Per-column filters (v1.7+)

The filter-row toggle in the toolbar (small filter icon) reveals
a row of inputs under the column headers. Type to narrow the
list without losing your row selection. The filters compose with
search, scope, and severity — all are ANDed together.

The filterable columns are:

- **File** — substring match against the full path.
- **Codec** — substring match against the video codec.
- **Container** — exact match (matroska, mp4, …).
- **Ext** — exact match. You can type with or without a leading
 dot (`.mkv` and `mkv` both work).

## Multi-select and bulk actions

Click row checkboxes to select files. The selection actions bar
appears above the table when at least one row is selected.

Multi-select **does not** clear your filters — toggling row
selection is independent of every filter input.

## Dashboard deep-links

The dashboard's **Open issues** card links here with the
actionable severities (`warn`, `high`, `error`, `crit`)
pre-filtered. The **Categories** card links here with the
matching codec or container filter applied.

## See also

- [Rules](/help/rules.overview) — the engine that drives the
 severity column.
- [Settings → Scanner](/help/settings.scanner) — controls what
 the scanner considers "media" and what it skips.
