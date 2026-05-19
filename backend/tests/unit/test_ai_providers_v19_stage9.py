"""v1.9 Stage 9.3 — AI provider tests.

Pins:
  * Provider Protocol satisfaction (kind attribute, chat method).
  * Per-provider wire shape — request URL, auth header, body
    contains messages/model/temperature/max_tokens.
  * OpenAI / Anthropic error without an api_key.
  * Anthropic extracts ``system`` messages into the separate
    field (its API requires that shape).
  * Custom OpenAPI provider works without an api_key (self-hosted
    servers often skip auth).
  * Factory ``get_ai_provider`` resolves all four kinds; unknown
    kind raises ValueError.
  * ``list_known_provider_kinds`` returns stable sorted list.
  * Helpers — ``_anonymize_path`` longest-first; nested library
    rewrites before parent; non-matching paths unchanged.
  * ``_extract_proposals`` parses bare JSON, ```json fences,
    bracket-bracket fallback; returns [] on garbage.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.ai import providers as providers_mod
from app.services.ai.providers import (
    AIProvider,
    AIProviderConfig,
    AnthropicProvider,
    ChatMessage,
    CustomOpenAPIProvider,
    OllamaProvider,
    OpenAIProvider,
    get_ai_provider,
    list_known_provider_kinds,
)
from app.services.ai.suggestions import _anonymize_path, _extract_proposals


def _config(**overrides) -> AIProviderConfig:
    base = {
        "endpoint": "http://ai.test",
        "model": "test-model",
        "api_key": "sk-test",
        "temperature": 0.2,
        "max_tokens": 256,
    }
    base.update(overrides)
    return AIProviderConfig(**base)


def _provider_with_transport(provider, transport: httpx.MockTransport):
    """Monkey-patch async_client to use the MockTransport.

    ``providers.py`` imports ``async_client`` at module level
    so we replace it for the duration of the test. The module
    reference is imported at file scope as ``providers_mod`` so
    CodeQL's ``py/import-and-import-from`` rule doesn't trip on
    the duplicate import shape."""
    orig = providers_mod.async_client

    def patched(**kwargs):
        c = orig(**kwargs)
        c._transport = transport  # type: ignore[attr-defined]
        return c

    providers_mod.async_client = patched  # type: ignore[assignment]
    return provider, lambda: setattr(providers_mod, "async_client", orig)


# ── Protocol satisfaction ───────────────────────────────────────


def test_all_providers_satisfy_protocol() -> None:
    for klass in (
        OllamaProvider,
        OpenAIProvider,
        AnthropicProvider,
        CustomOpenAPIProvider,
    ):
        instance = klass()
        assert isinstance(instance, AIProvider)
        assert instance.kind in {
            "ollama",
            "openai",
            "anthropic",
            "custom_openapi",
        }


# ── Ollama ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ollama_posts_to_api_chat() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "message": {"role": "assistant", "content": "hi"},
                "prompt_eval_count": 12,
                "eval_count": 5,
            },
        )

    provider, restore = _provider_with_transport(
        OllamaProvider(), httpx.MockTransport(handler)
    )
    try:
        result = await provider.chat(
            _config(),
            [ChatMessage(role="user", content="hello")],
        )
    finally:
        restore()
    assert "/api/chat" in captured["url"]
    assert captured["body"]["model"] == "test-model"
    assert captured["body"]["stream"] is False
    assert captured["body"]["options"]["temperature"] == 0.2
    assert captured["body"]["options"]["num_predict"] == 256
    assert result.content == "hi"
    assert result.prompt_tokens == 12
    assert result.completion_tokens == 5
    assert result.total_tokens == 17


@pytest.mark.asyncio
async def test_ollama_works_without_api_key() -> None:
    """Ollama is typically local + unauthenticated. Missing
    api_key must not block the call."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"content": "ok"}, "prompt_eval_count": 0},
        )

    provider, restore = _provider_with_transport(
        OllamaProvider(), httpx.MockTransport(handler)
    )
    try:
        result = await provider.chat(
            _config(api_key=None),
            [ChatMessage(role="user", content="hi")],
        )
    finally:
        restore()
    assert result.content == "ok"


# ── OpenAI ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_openai_posts_to_chat_completions() -> None:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["headers"] = dict(req.headers)
        captured["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "model": "gpt-4o",
                "choices": [
                    {"message": {"role": "assistant", "content": "[]"}}
                ],
                "usage": {"prompt_tokens": 50, "completion_tokens": 2},
            },
        )

    provider, restore = _provider_with_transport(
        OpenAIProvider(), httpx.MockTransport(handler)
    )
    try:
        result = await provider.chat(
            _config(),
            [ChatMessage(role="user", content="propose")],
        )
    finally:
        restore()
    assert "/v1/chat/completions" in captured["url"]
    assert captured["headers"].get("authorization") == "Bearer sk-test"
    assert captured["body"]["temperature"] == 0.2
    assert captured["body"]["max_tokens"] == 256
    assert result.content == "[]"
    assert result.prompt_tokens == 50
    assert result.completion_tokens == 2


@pytest.mark.asyncio
async def test_openai_raises_without_api_key() -> None:
    provider = OpenAIProvider()
    with pytest.raises(ValueError, match="api_key"):
        await provider.chat(
            _config(api_key=None),
            [ChatMessage(role="user", content="x")],
        )


@pytest.mark.asyncio
async def test_openai_empty_choices_returns_empty_content() -> None:
    """OpenAI sometimes returns an empty choices array on
    moderation flags. Should surface as empty content, not
    crash."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    provider, restore = _provider_with_transport(
        OpenAIProvider(), httpx.MockTransport(handler)
    )
    try:
        result = await provider.chat(
            _config(),
            [ChatMessage(role="user", content="x")],
        )
    finally:
        restore()
    assert result.content == ""


# ── Anthropic ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_anthropic_extracts_system_messages() -> None:
    """Anthropic's API requires the system content in a separate
    ``system`` field rather than as a message with role=system."""
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["headers"] = dict(req.headers)
        captured["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "model": "claude-3-5-sonnet",
                "content": [{"type": "text", "text": "[]"}],
                "usage": {"input_tokens": 100, "output_tokens": 3},
            },
        )

    provider, restore = _provider_with_transport(
        AnthropicProvider(), httpx.MockTransport(handler)
    )
    try:
        result = await provider.chat(
            _config(),
            [
                ChatMessage(role="system", content="be concise"),
                ChatMessage(role="user", content="hi"),
            ],
        )
    finally:
        restore()
    assert "/v1/messages" in captured["url"]
    assert captured["headers"].get("x-api-key") == "sk-test"
    assert captured["headers"].get("anthropic-version") == "2023-06-01"
    # system field carries the system content; messages array
    # carries only non-system.
    assert captured["body"]["system"] == "be concise"
    assert all(m["role"] != "system" for m in captured["body"]["messages"])
    assert result.content == "[]"
    assert result.prompt_tokens == 100
    assert result.completion_tokens == 3


@pytest.mark.asyncio
async def test_anthropic_concatenates_multiple_text_blocks() -> None:
    """The Anthropic response's ``content`` is a list of typed
    blocks. We concatenate text-type blocks and ignore others."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "claude",
                "content": [
                    {"type": "text", "text": "part1 "},
                    {"type": "tool_use", "input": {}},  # ignored
                    {"type": "text", "text": "part2"},
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    provider, restore = _provider_with_transport(
        AnthropicProvider(), httpx.MockTransport(handler)
    )
    try:
        result = await provider.chat(
            _config(), [ChatMessage(role="user", content="x")]
        )
    finally:
        restore()
    assert result.content == "part1 part2"


@pytest.mark.asyncio
async def test_anthropic_raises_without_api_key() -> None:
    provider = AnthropicProvider()
    with pytest.raises(ValueError, match="api_key"):
        await provider.chat(
            _config(api_key=None),
            [ChatMessage(role="user", content="x")],
        )


# ── Custom OpenAPI ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_custom_openapi_works_without_api_key() -> None:
    """Self-hosted endpoints (text-generation-webui, vLLM, LM
    Studio) frequently skip auth. The provider must not require
    an api_key."""
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(req.headers)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "[]"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    provider, restore = _provider_with_transport(
        CustomOpenAPIProvider(), httpx.MockTransport(handler)
    )
    try:
        await provider.chat(
            _config(api_key=None),
            [ChatMessage(role="user", content="x")],
        )
    finally:
        restore()
    # No Authorization header sent when api_key is None.
    assert "authorization" not in {k.lower() for k in captured["headers"]}


# ── Factory ─────────────────────────────────────────────────────


def test_factory_resolves_all_known_kinds() -> None:
    for kind in ("ollama", "openai", "anthropic", "custom_openapi"):
        provider = get_ai_provider(kind)
        assert provider.kind == kind


def test_factory_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="Unknown AI provider"):
        get_ai_provider("does-not-exist")


def test_list_known_provider_kinds_returns_sorted_unique() -> None:
    kinds = list_known_provider_kinds()
    assert kinds == sorted(set(kinds))
    assert set(kinds) == {"ollama", "openai", "anthropic", "custom_openapi"}


# ── Helpers — _anonymize_path ───────────────────────────────────


def test_anonymize_path_replaces_library_root() -> None:
    out = _anonymize_path(
        "/mnt/media/Movies/Film.mkv",
        [("/mnt/media/Movies", "<library>")],
    )
    assert out == "<library>/Film.mkv"


def test_anonymize_path_longest_first_wins() -> None:
    """Nested library — substitution list is built longest-first
    by the caller, so the nested root wins over the parent."""
    subs = [
        ("/mnt/media/Movies/4K", "<library:4k>"),
        ("/mnt/media/Movies", "<library:standard>"),
    ]
    assert (
        _anonymize_path("/mnt/media/Movies/4K/Film.mkv", subs)
        == "<library:4k>/Film.mkv"
    )
    assert (
        _anonymize_path("/mnt/media/Movies/Film.mkv", subs)
        == "<library:standard>/Film.mkv"
    )


def test_anonymize_path_unmatched_path_unchanged() -> None:
    assert (
        _anonymize_path(
            "/other/path/x.mkv", [("/mnt/media/Movies", "<library>")]
        )
        == "/other/path/x.mkv"
    )


def test_anonymize_path_exact_root_match() -> None:
    """The path exactly matches the root with no trailing slash
    — substitute the placeholder cleanly."""
    assert (
        _anonymize_path(
            "/mnt/media/Movies", [("/mnt/media/Movies", "<library>")]
        )
        == "<library>"
    )


# ── Helpers — _extract_proposals ────────────────────────────────


def test_extract_proposals_parses_bare_json_array() -> None:
    out = _extract_proposals('[{"name": "x"}]')
    assert out == [{"name": "x"}]


def test_extract_proposals_strips_fenced_code() -> None:
    body = (
        "Sure, here you go:\n\n"
        "```json\n"
        '[{"name": "alpha"}, {"name": "beta"}]\n'
        "```"
    )
    out = _extract_proposals(body)
    assert [p["name"] for p in out] == ["alpha", "beta"]


def test_extract_proposals_handles_bracket_bracket_fallback() -> None:
    """LLM ignored the no-prose constraint and wrote prose
    around a raw array. We find the outermost [...] and parse
    that."""
    body = (
        "OK, my proposals are: [{\"name\": \"x\"}] — let me know "
        "what you think."
    )
    out = _extract_proposals(body)
    assert out == [{"name": "x"}]


def test_extract_proposals_returns_empty_on_garbage() -> None:
    assert _extract_proposals("not json") == []
    assert _extract_proposals("") == []


def test_extract_proposals_returns_empty_when_root_not_array() -> None:
    """JSON parses but root is an object, not an array — return
    []. The system prompt says "array only"."""
    assert _extract_proposals('{"name": "x"}') == []


def test_extract_proposals_filters_non_dict_entries() -> None:
    out = _extract_proposals('["bad", null, {"name": "good"}, 42]')
    assert out == [{"name": "good"}]
