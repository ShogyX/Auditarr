---
id: rules/search-upstream
title: search_upstream action
category: rules
tags: [rules, actions, sonarr, radarr, bazarr]
summary: Trigger a search on a connected upstream integration when a rule matches.
help_context: [rules.search-upstream]
related: [rules/actions, integrations/sonarr, integrations/radarr, integrations/bazarr]
---

# `search_upstream` action

When a rule matches, `search_upstream` triggers a search on a connected Sonarr, Radarr, or Bazarr instance for the matched file. Use it to automate the "this file is broken / orphaned / missing subs — re-find it from the indexers" workflow.

## Action shape

```json
{
  "type": "search_upstream",
  "target": "sonarr",
  "integration_id": "<the integration row's id>"
}
```

* `target` — one of `sonarr`, `radarr`, `bazarr`. Redundant with the integration row's `kind` field but kept for explicit auditing of what the rule expected to call.
* `integration_id` — the configured-integrations row to call. The visual editor populates this as a dropdown of matching enabled integrations.

## Example use cases

* **Orphaned file in a Sonarr-managed library** — `(is_orphaned == true) → search_upstream(sonarr)`. Sonarr re-searches the series and re-grabs the missing episode.
* **Missing subtitles** — `(has_subtitles == false AND audio_languages contains "eng") → search_upstream(bazarr)`. Bazarr searches its providers for English subs.
* **Wrong codec** — `(video_codec == "av1") → search_upstream(radarr)`. Radarr re-grabs an h264/hevc release if the operator's player can't direct-play AV1.

## Deduplication

Multiple matching rules with `search_upstream` actions for the same file and same integration produce one search call. The deduplication key is `(integration_id, media_file_id)`.

## Audit trail

Every `search_upstream` action that fires writes an `AuditLogEntry` row tagged `rule.search_upstream` with the matched rule id, file id, integration id, and the upstream's HTTP status. Operators can review the log under `System → Logs`.

## Error handling

* If the integration is disabled or in `error` health state, the action is logged as `skipped` with a reason. Other actions on the same rule still run.
* If the upstream call returns 4xx (other than auth-related), the rule logs the failure but does not retry. The next rule evaluation that matches will try again.
* If the upstream call returns 5xx or a transient auth error (401/429), the call is enqueued for retry on the standard worker backoff.
