---
id: dashboard/ai-suggestions
title: Rule suggestions
category: dashboard
tags: [dashboard, rules, suggestions, ai]
summary: Heuristic and AI-generated rule proposals from your library's behavior.
help_context: [dashboard.suggestions]
related: [rules/overview, integrations/ai-providers]
---

# Rule suggestions

The Suggestions card surfaces proposed rules that Auditarr's analyzer thinks would be useful, based on patterns it sees in playback and library data. Every suggestion is operator-reviewed; nothing auto-deploys.

## Where suggestions come from

There are two generators feeding the same suggestion list:

### Heuristic generator

Runs after every playback-analyzer pass. Looks at:

* Direct-play ratio per `(video_codec, container)` triple.
* Repeat-transcode patterns — files transcoded by the same client more than N times.
* Subtitle-missing patterns — files that played without subtitles when an operator has Bazarr connected.
* Stale-rule signals — rules that haven't matched any file in 30+ days suggest dismissal.

Each suggestion includes the evidence the heuristic saw (top files, device names, counts) so an operator can verify before deploying.

### AI generator

When an AI-provider integration is connected and enabled, the dashboard offers a "Generate from library" button. Clicking it sends a context payload to the configured provider (Ollama, OpenAI, Anthropic, or a custom OpenAPI endpoint) and asks for proposed rules.

The context payload is privacy-conscious: file paths have library roots replaced with `<library>/`, no playback usernames are sent, and the operator's API key is the only personal token in the request.

The provider returns structured JSON with one or more `RuleDefinition` shapes, each validated against the rule schema before being persisted as a suggestion. AI suggestions carry an `AI` badge alongside the heuristic name.

## Reviewing and deploying a suggestion

Clicking a suggestion row opens the **Suggestion review** modal:

* The proposed rule definition rendered in the visual builder
* A dry-run preview showing how many files the rule would match if deployed
* Evidence (for heuristic) or the model's reasoning (for AI)
* Three buttons: **Deploy as enabled**, **Deploy as disabled** (review-only), **Dismiss**

Dismissed suggestions are remembered. The next AI call includes the dismissed list in the prompt so the same bad idea doesn't come back. Heuristic re-runs also respect the dismissed set.

## Privacy and cost guards

* External provider sends use anonymized paths. Operators can opt out of external send entirely (Ollama or a custom-local endpoint stays local).
* Per-call token cap (`max_tokens`) and per-day call budget (`ai_call_budget`) on the integration. Budget exceeded → fall back to heuristic suggestions, surface a banner.
* The integration's API key is encrypted at rest using the application secret key; the test handler under Settings → Secrets makes a low-token round-trip to validate the credential.
