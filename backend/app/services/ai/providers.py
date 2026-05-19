"""v1.9 Stage 9.3 — AI provider abstraction.

Defines the ``AIProvider`` Protocol and four concrete
implementations matching the operator-selectable kinds:

  * ``ollama``        — local llama.cpp / Ollama server
  * ``openai``        — OpenAI chat completions API
  * ``anthropic``     — Anthropic messages API
  * ``custom_openapi`` — operator-supplied endpoint that mimics
                        the OpenAI chat shape (the most common
                        compat target for self-hosted servers)

All providers conform to a single ``chat(messages)`` interface
that returns a ``ChatResult``. Each provider handles its own
authentication and response shape.

Privacy + cost guards (Stage 9.4):

  * ``max_tokens`` and ``temperature`` come from the provider's
    Auditarr Integration config; the provider passes them to
    the upstream verbatim.
  * Per-day call budget enforcement happens in the suggestion-
    generator service (not here) — the provider just executes
    requests it's handed.
  * Anonymization is the caller's responsibility (the
    suggestion service rewrites file paths before building
    the messages payload).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


from app.core.http import async_client


@dataclass(slots=True)
class ChatMessage:
    """One message in a chat exchange. Mirrors the OpenAI
    chat-message shape (which Anthropic and most local servers
    converged on). ``role`` is one of ``system``, ``user``,
    ``assistant``."""

    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(slots=True)
class ChatResult:
    """Provider response. ``content`` is the assistant message
    text (the suggestion service parses structured JSON out of
    it). Token counts are best-effort — providers that don't
    surface them leave the values at 0."""

    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(slots=True)
class AIProviderConfig:
    """v1.9 Stage 9.3 — operator configuration passed to a
    provider call. Built from the AI provider Integration's
    config + secrets by the suggestion service."""

    endpoint: str
    model: str
    api_key: str | None = None
    temperature: float = 0.2
    max_tokens: int = 1024
    # Operator-set per-day budget. Enforced by the suggestion
    # service; surfaced here for providers that want to reflect
    # it in their request (e.g. setting a hard token cap).
    daily_call_budget: int | None = None


@runtime_checkable
class AIProvider(Protocol):
    """All AI providers conform to this single-call interface.
    The kind string identifies which provider implementation
    handles a given Integration row."""

    kind: str

    async def chat(
        self, config: AIProviderConfig, messages: list[ChatMessage]
    ) -> ChatResult:
        pass


# ── Implementations ──────────────────────────────────────────────


class OllamaProvider:
    """Ollama / llama.cpp server. POST /api/chat with the
    OpenAI-style messages array. Auth is optional (typically
    none — operator runs Ollama locally)."""

    kind = "ollama"

    async def chat(
        self,
        config: AIProviderConfig,
        messages: list[ChatMessage],
    ) -> ChatResult:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        body = {
            "model": config.model,
            "messages": [m.to_dict() for m in messages],
            "stream": False,
            "options": {
                "temperature": config.temperature,
                "num_predict": config.max_tokens,
            },
        }
        async with async_client(
            base_url=config.endpoint.rstrip("/"),
            headers=headers,
            timeout=120.0,
        ) as client:
            response = await client.post("/api/chat", json=body)
            response.raise_for_status()
            payload = response.json() or {}
        message = payload.get("message") or {}
        return ChatResult(
            content=str(message.get("content") or ""),
            prompt_tokens=int(payload.get("prompt_eval_count") or 0),
            completion_tokens=int(payload.get("eval_count") or 0),
            model=str(payload.get("model") or config.model),
            raw=payload,
        )


class OpenAIProvider:
    """OpenAI chat completions. POST /v1/chat/completions with
    Bearer auth."""

    kind = "openai"

    async def chat(
        self,
        config: AIProviderConfig,
        messages: list[ChatMessage],
    ) -> ChatResult:
        if not config.api_key:
            raise ValueError("OpenAI provider requires an api_key")
        body = {
            "model": config.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }
        async with async_client(
            base_url=config.endpoint.rstrip("/"),
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        ) as client:
            response = await client.post("/v1/chat/completions", json=body)
            response.raise_for_status()
            payload = response.json() or {}
        choices = payload.get("choices") or []
        if not choices:
            return ChatResult(content="", model=config.model, raw=payload)
        first = choices[0]
        msg = first.get("message") or {}
        usage = payload.get("usage") or {}
        return ChatResult(
            content=str(msg.get("content") or ""),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            model=str(payload.get("model") or config.model),
            raw=payload,
        )


class AnthropicProvider:
    """Anthropic messages API. POST /v1/messages with x-api-key
    + anthropic-version header. The Anthropic message shape
    requires a separate ``system`` field rather than a system
    message in the messages array — we extract it here."""

    kind = "anthropic"

    async def chat(
        self,
        config: AIProviderConfig,
        messages: list[ChatMessage],
    ) -> ChatResult:
        if not config.api_key:
            raise ValueError("Anthropic provider requires an api_key")
        system_msgs = [m.content for m in messages if m.role == "system"]
        non_system = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role != "system"
        ]
        body: dict[str, Any] = {
            "model": config.model,
            "messages": non_system,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
        }
        if system_msgs:
            body["system"] = "\n\n".join(system_msgs)

        async with async_client(
            base_url=config.endpoint.rstrip("/"),
            headers={
                "x-api-key": config.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        ) as client:
            response = await client.post("/v1/messages", json=body)
            response.raise_for_status()
            payload = response.json() or {}

        # The Anthropic response's ``content`` is a list of
        # blocks; we concatenate text blocks. Other block types
        # (tool_use, image) are ignored — the suggestion service
        # asks for plain JSON in text.
        content_blocks = payload.get("content") or []
        text_parts: list[str] = []
        for block in content_blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(str(block.get("text") or ""))
        usage = payload.get("usage") or {}
        return ChatResult(
            content="".join(text_parts),
            prompt_tokens=int(usage.get("input_tokens") or 0),
            completion_tokens=int(usage.get("output_tokens") or 0),
            model=str(payload.get("model") or config.model),
            raw=payload,
        )


class CustomOpenAPIProvider:
    """Operator-supplied endpoint that mimics the OpenAI shape.
    The most common compat surface for self-hosted servers
    (text-generation-webui, vLLM, LM Studio, …). Same wire
    contract as ``OpenAIProvider`` — we just don't assume
    the api_key is present."""

    kind = "custom_openapi"

    async def chat(
        self,
        config: AIProviderConfig,
        messages: list[ChatMessage],
    ) -> ChatResult:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        body = {
            "model": config.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }
        async with async_client(
            base_url=config.endpoint.rstrip("/"),
            headers=headers,
            timeout=60.0,
        ) as client:
            response = await client.post("/v1/chat/completions", json=body)
            response.raise_for_status()
            payload = response.json() or {}
        choices = payload.get("choices") or []
        if not choices:
            return ChatResult(content="", model=config.model, raw=payload)
        first = choices[0]
        msg = first.get("message") or {}
        usage = payload.get("usage") or {}
        return ChatResult(
            content=str(msg.get("content") or ""),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            model=str(payload.get("model") or config.model),
            raw=payload,
        )


# ── Factory ─────────────────────────────────────────────────────


_PROVIDERS: dict[str, type[AIProvider]] = {
    "ollama": OllamaProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "custom_openapi": CustomOpenAPIProvider,
}


def get_ai_provider(kind: str) -> AIProvider:
    """Resolve a provider kind string to a fresh provider
    instance. Raises ValueError on unknown kinds — the AI
    provider Integration's config_schema gates this at the
    operator UI, but defensive at the API layer too."""
    klass = _PROVIDERS.get(kind)
    if klass is None:
        raise ValueError(f"Unknown AI provider kind: {kind!r}")
    return klass()


def list_known_provider_kinds() -> list[str]:
    """Stable ordering — UI dropdowns iterate this for the kind
    selector."""
    return sorted(_PROVIDERS.keys())


__all__ = [
    "AIProvider",
    "AIProviderConfig",
    "AnthropicProvider",
    "ChatMessage",
    "ChatResult",
    "CustomOpenAPIProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "get_ai_provider",
    "list_known_provider_kinds",
]
