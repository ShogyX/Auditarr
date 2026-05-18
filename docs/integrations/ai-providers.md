---
id: integrations/ai-providers
title: AI providers
category: integrations
tags: [integrations, ai, llm, ollama, openai, anthropic]
summary: Wire Auditarr to an LLM for AI-generated rule suggestions.
help_context: [integrations.ai-providers]
related: [dashboard/ai-suggestions, rules/ai-authoring]
---

# AI providers

Auditarr can use an LLM to generate rule suggestions from your library's behavior. The provider runs as a regular integration row with `kind=ai-provider`. Four provider kinds ship out of the box; all conform to a single `chat(messages)` interface so the calling code doesn't know which one it's talking to.

## Provider kinds

* **Ollama** — local model server. No API key needed; endpoint usually `http://localhost:11434`. The lowest-friction option for operators who don't want to send library context to a hosted service.
* **OpenAI** — `gpt-4o` or `gpt-4o-mini` recommended. Requires an API key with permission for the named model.
* **Anthropic** — `claude-3-5-sonnet` or `claude-3-5-haiku` recommended. API key from `console.anthropic.com`.
* **Custom OpenAPI** — point at any OpenAI-compatible endpoint (LiteLLM proxy, vLLM, LocalAI, a hosted vendor that mimics OpenAI's wire shape). Most local-LLM gateways implement this contract.

## Configuration

Under `Integrations → New connector → AI provider`. The form fields:

| Field | Purpose |
|---|---|
| `provider_kind` | One of `ollama`, `openai`, `anthropic`, `custom_openapi`. |
| `endpoint` | Base URL of the provider. Hosted defaults are populated for OpenAI/Anthropic. |
| `model` | Model name as the provider expects it (e.g. `llama3.2`, `gpt-4o-mini`, `claude-3-5-sonnet-20241022`). |
| `api_key` | Secret. Stored encrypted with the application key; visible only at write time. |
| `temperature` | Default `0.2` — low because rule generation is structured-output and creativity isn't useful. |
| `max_tokens` | Per-call cap. Default `2000`. |
| `ai_call_budget` | Per-day call budget. When exceeded, the dashboard falls back to heuristic suggestions and surfaces a banner. |

The integration's secret test handler does a low-token chat round-trip (`"return the word OK"`-class) to validate that the credential and endpoint actually work before saving.

## Multiple providers

Operators can configure more than one AI provider. The dashboard's "Generate from library" button shows a picker when more than one is enabled. The most-recent provider is used by default.

## Privacy

* File paths in the context payload are anonymized: the library root is replaced with `<library>/` so the model sees `<library>/Inception (2010)/Inception.2010.mkv` rather than the operator's real path.
* No playback usernames are sent.
* The API key is the only personal token in the request — it lives in the integration row, encrypted at rest, and is sent only over TLS.

## Cost guards

* `max_tokens` caps each call's response size.
* `ai_call_budget` caps total calls per day. Exceeded → graceful fallback to heuristics.
* The provider's reported `usage.total_tokens` is recorded per call on the `RuleSuggestion` row's `ai_usage` field so operators can audit spend.

## Wiring AI suggestions

See [Rule suggestions](../dashboard/ai-suggestions.md) for how the suggestions land on the dashboard and how operators deploy or dismiss them.
