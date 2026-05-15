---
id: overview
title: Auditarr overview
category: guide
tags: [getting-started]
summary: What Auditarr is and how the pieces fit together.
help_context: [dashboard.overview]
related: [guide/quickstart, guide/architecture]
---

# Auditarr overview

Auditarr audits a media library against a set of rules and surfaces issues
that need attention — missing subtitles, codec mismatches, oversized files,
optimization candidates, and so on. It connects to the rest of your
self-hosted stack (Plex, Jellyfin, Sonarr, Radarr, Bazarr, Tdarr) through
official APIs and uses a plugin system to add new integrations without
touching core code.

## The shape of a run

1. **Scan** — Auditarr enumerates files in your library roots.
2. **Classify** — `ffprobe` extracts technical metadata.
3. **Evaluate** — the rules engine matches each file against enabled rules.
4. **Act** — automations send notifications, queue optimizations, or invoke
   webhooks.
5. **Report** — the dashboard summarises severity, codec mix, integration
   health, and recent activity.

You don't need to use every step; rules can be informational only.
