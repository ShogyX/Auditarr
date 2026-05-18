---
id: rules/templates
title: Rule templates
category: rules
tags: [rules, templates, library]
summary: Ready-to-clone rule bodies for common operator scenarios.
help_context: [rules.templates]
related: [rules/overview, rules/actions, rules/conditions]
---

# Rule templates

Templates are pre-authored rule bodies the operator can clone into their own writable custom rules. They live alongside the built-in rules but serve a different purpose: built-ins are codebase-owned and cannot be edited; templates are starting points that become editable the moment they're cloned.

## Templates tab

Open `Rules → Templates`. Each template card shows:

* **Name + summary** — what the rule does in plain language.
* **Definition preview** — the matching conditions and actions, rendered in the same visual style as a saved rule.
* **Tags** — a few keywords for filtering (e.g. `plex`, `subtitles`, `cleanup`).
* **Clone** button — creates a custom rule from this template, opened in the editor for the operator to tweak before enabling.

The cloned rule starts **disabled** so the operator can review and adjust before it fires.

## Available templates

Bundled templates (subject to expansion):

| Template | What it does |
|---|---|
| Plex-incompatible video | Tags files whose video codec isn't in Plex's direct-play set. |
| Plex-incompatible audio | Tags files with audio codecs Plex can't direct-play to the configured target platforms. |
| Jellyfin-incompatible video | Tags files whose video codec isn't in Jellyfin's direct-play set. |
| Subtitle-missing | Warns on media files with no embedded or sidecar subtitle track. |
| Stale-and-orphaned | Marks files flagged orphaned for more than 30 days for delete review. |
| Fat HEVC | Warns on HEVC files above a configurable bitrate (default 20 Mbps). |
| 4K direct-play check | Warns on 4K files whose codec/container combination none of the observed devices direct-play. |

## Re-seed

The templates list is loaded from the codebase on startup. If a template was edited and the operator wants to reset to the bundled set, the **Re-seed templates** button under the Templates tab refreshes the on-disk list. Re-seeding does not delete any rules the operator has already cloned; only the template catalog is reset.

## Authoring templates

Operators with shell access can drop new templates into `backend/plugins/rule_templates/`. Each template is a JSON file matching the `RuleTemplate` shape. The next backend restart picks them up; the **Re-seed templates** button surfaces them in the UI without a restart.
