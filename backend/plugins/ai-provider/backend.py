"""v1.9 Stage 9.3 — AI provider integration plugin.

The plugin registers ``kind="ai-provider"`` as a configurable
Integration. Unlike Sonarr / Plex / Tracearr (which are
single-vendor connectors), this one is a shell whose
``config.provider_kind`` selects which of the four supported
LLM backends actually handles requests
(``ollama`` | ``openai`` | ``anthropic`` | ``custom_openapi``).

The plugin itself is intentionally minimal — its
``healthcheck`` just smoke-tests the configured endpoint via
the matching ``AIProvider`` from ``app.services.ai.providers``.
Library discovery / tag sync / playback events / search /
transcode submit are all no-ops because an LLM provider
doesn't carry that concept.

The real work happens in ``AISuggestionService`` (Stage 9.3),
which the ``POST /rules/suggestions/ai-generate`` endpoint
drives.
"""

from __future__ import annotations

from typing import Any

from app.integrations.types import (
    DiscoveredLibrary,
    HealthReport,
    IntegrationConfig,
    IntegrationProvider,
    PlaybackEventDTO,
    SearchTriggerResult,
    TagSync,
)
from app.plugins import Plugin, PluginContext
from app.services.ai.providers import (
    AIProviderConfig,
    ChatMessage,
    get_ai_provider,
    list_known_provider_kinds,
)


class AIProviderIntegration(IntegrationProvider):
    kind = "ai-provider"
    label = "AI Provider"
    config_schema: dict[str, Any] = {
        "type": "object",
        "required": ["provider_kind", "endpoint", "model"],
        "properties": {
            "provider_kind": {
                "type": "string",
                "title": "Provider",
                "enum": list_known_provider_kinds(),
                "description": (
                    "Which LLM backend to call. Ollama and "
                    "custom_openapi are typically local; openai and "
                    "anthropic require an API key (the operator's "
                    "data leaves the network)."
                ),
                "default": "ollama",
            },
            "endpoint": {
                "type": "string",
                "title": "Endpoint URL",
                "description": (
                    "Base URL for the provider. Examples: "
                    "http://localhost:11434 for Ollama, "
                    "https://api.openai.com for OpenAI, "
                    "https://api.anthropic.com for Anthropic."
                ),
            },
            "model": {
                "type": "string",
                "title": "Model name",
                "description": (
                    "Provider-specific model identifier "
                    "(e.g. llama3, gpt-4o, claude-3-5-sonnet)."
                ),
            },
            "temperature": {
                "type": "number",
                "title": "Temperature",
                "default": 0.2,
                "minimum": 0.0,
                "maximum": 2.0,
            },
            "max_tokens": {
                "type": "integer",
                "title": "Max tokens",
                "default": 1024,
                "minimum": 64,
                "maximum": 32_000,
            },
            "daily_call_budget": {
                "type": "integer",
                "title": "Daily call budget",
                "description": (
                    "Maximum calls to this provider per 24 hour "
                    "window. Exceeded → suggestion generator falls "
                    "back to heuristic-only mode."
                ),
                "default": 50,
                "minimum": 1,
                "maximum": 10_000,
            },
            "send_paths_external": {
                "type": "boolean",
                "title": "Send file paths to external provider",
                "description": (
                    "When False, file paths sent to the AI are "
                    "redacted entirely (only counts + library "
                    "kinds remain). Strict-privacy operators "
                    "should turn this off for openai / anthropic."
                ),
                "default": True,
            },
        },
    }
    secret_fields: tuple[str, ...] = ("api_key",)

    def __init__(self, log: Any = None) -> None:
        self._log = log

    async def healthcheck(
        self, config: IntegrationConfig
    ) -> HealthReport:
        """Smoke-test the configured endpoint with a minimal
        chat call. Failure surfaces as ``error`` with the
        provider's exception detail; the AI suggestion
        generator surface uses the same plumbing on every
        call so this is a good early-warning gate."""
        provider_kind = str(
            config.options.get("provider_kind") or "ollama"
        )
        try:
            provider = get_ai_provider(provider_kind)
        except ValueError as exc:
            return HealthReport(status="error", detail=str(exc))

        ai_config = AIProviderConfig(
            endpoint=str(config.options.get("endpoint") or ""),
            model=str(config.options.get("model") or ""),
            api_key=str(config.secrets.get("api_key") or "") or None,
            temperature=0.0,
            max_tokens=16,
        )
        if not ai_config.endpoint or not ai_config.model:
            return HealthReport(
                status="error",
                detail="endpoint and model are required",
            )
        try:
            result = await provider.chat(
                ai_config,
                [
                    ChatMessage(
                        role="user",
                        content="Reply with the single token: OK",
                    )
                ],
            )
        except Exception as exc:  # noqa: BLE001
            return HealthReport(
                status="error",
                detail=f"{provider_kind} chat failed: {exc}",
            )
        return HealthReport(
            status="ok",
            detail=f"{provider_kind} @ {result.model}",
        )

    # ── No-ops ─────────────────────────────────────────────────
    async def discover_libraries(
        self, _config: IntegrationConfig
    ) -> list[DiscoveredLibrary]:
        return []

    async def sync_tags(
        self, _config: IntegrationConfig
    ) -> list[TagSync]:
        return []

    async def fetch_playback_events(
        self,
        _config: IntegrationConfig,
        _since: Any = None,
    ) -> list[PlaybackEventDTO]:
        return []

    async def trigger_search(
        self,
        _config: IntegrationConfig,
        _media_file_path: str,
    ) -> SearchTriggerResult:
        return SearchTriggerResult(
            status="error",
            detail="AI provider integrations don't accept search commands",
        )


def register(context: PluginContext) -> Plugin:
    context.register_integration(AIProviderIntegration(log=context.logger()))
    return Plugin(context)
