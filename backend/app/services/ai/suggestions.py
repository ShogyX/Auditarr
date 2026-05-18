"""v1.9 Stage 9.3 — AI suggestion generator.

Pulls together:
  * the device index (Stage 9.1) — what's actually being used
  * the top transcoded files in the analysis window
  * the operator's currently-active rules — so the AI doesn't
    propose duplicates
  * rejected-suggestion history — so the AI doesn't propose
    things the operator's already shot down
  * the rule DSL surface — fields, operators, actions

…builds a single chat completion request to the operator's
selected AI provider, validates the returned JSON against the
RuleDefinition schema, and persists each valid proposal as a
``RuleSuggestion`` with ``heuristic="ai_<provider>"``.

Privacy + cost guards (Stage 9.4):

  * File paths sent to external providers are anonymized:
    ``/mnt/media/Movies/Film.mkv`` becomes
    ``<library>/Film.mkv``. Operators who don't want paths
    leaving their network configure an ``ollama`` or
    ``custom_openapi`` provider; an attribute on the provider
    integration (``send_paths_external``) gates path inclusion
    for the strict-privacy case.
  * Per-day call budget enforced before the HTTP call. Exceeded
    → fall back to heuristic suggestions and surface a banner
    (the banner is the API response shape; the caller renders
    it).
  * Per-call ``max_tokens`` enforced by passing to the provider.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.audit_log import AuditLogEntry
from app.models.integration import Integration
from app.models.library import Library
from app.models.playback import PlaybackEvent
from app.models.playback_device import PlaybackDevice
from app.models.rule import Rule
from app.models.rule_suggestion import RuleSuggestion
from app.rules.schema import RuleDefinition
from app.security.secrets import get_secret_box
from app.services.ai.providers import (
    AIProviderConfig,
    ChatMessage,
    get_ai_provider,
)
from app.utils.datetime import utcnow

log = get_logger("auditarr.ai.suggestions", category="ai")

DEFAULT_DAILY_BUDGET = 50
TOP_FILES_LIMIT = 50
SYSTEM_PROMPT = """\
You are an assistant that proposes rules for a media library
auditing tool called Auditarr. Each rule has a ``match`` block
(field/operator/value or all/any/not composites) and a list of
``actions`` (set_severity, add_tag, queue_optimization, notify,
delete, vt_lookup, search_upstream).

Constraints — your proposals MUST follow these:

  1. Output ONLY a JSON array of proposed rules. No prose,
     no markdown fences. Each entry is an object with:
       - ``name``: short human-readable name
       - ``rationale``: one-sentence explanation
       - ``definition``: the RuleDefinition object the engine
         will execute
  2. Each ``definition`` MUST match this shape:
       { "match": <match block>, "actions": [<action>, ...] }
  3. NEVER propose a rule that's a duplicate or near-duplicate
     of one already active. The user will give you the active
     rules; check against them.
  4. NEVER propose a rule the user has previously REJECTED.
     The user will give you the rejection list.
  5. NEVER propose ``delete`` actions automatically. If a rule
     might delete files, set severity to ``high`` and emit a
     ``notify`` action instead.
  6. NEVER propose rules that would tag every file in the
     library (e.g. an empty match block). Match conditions
     must be specific.
  7. Prefer ``set_severity`` + ``add_tag`` over destructive
     actions. The user reviews every suggestion before it
     deploys.

If you cannot propose any rules, return an empty JSON array: [].
"""


@dataclass(slots=True)
class AISuggestResult:
    """Outcome surfaced by the API endpoint."""

    suggestions_created: int = 0
    candidates_received: int = 0
    candidates_rejected: int = 0
    budget_exceeded: bool = False
    provider_kind: str | None = None
    provider_integration_id: str | None = None
    error: str | None = None


class AISuggestionService:
    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session

    async def generate(
        self,
        *,
        provider_integration_id: str | None = None,
    ) -> AISuggestResult:
        """Produce AI suggestions using the operator's configured
        AI provider integration.

        When ``provider_integration_id`` is None, picks the first
        enabled provider Integration of kind ``ai_provider``. When
        the operator runs more than one, the API caller should
        pass the chosen id explicitly.
        """
        result = AISuggestResult()
        integration = await self._resolve_provider(
            provider_integration_id
        )
        if integration is None:
            result.error = (
                "No enabled AI provider integration configured. "
                "Add one in Integrations."
            )
            return result
        result.provider_integration_id = integration.id

        # v1.9 audit fix (AI-1): fail-fast on missing
        # provider_kind rather than silently defaulting to
        # ``openai`` — a misconfigured Ollama integration would
        # otherwise call the OpenAI wire shape against
        # localhost:11434.
        provider_kind = (
            integration.config.get("provider_kind")
            if integration.config
            else None
        )
        if not provider_kind:
            result.error = (
                "AI integration is missing ``provider_kind`` in its "
                "configuration. Edit the integration and pick one of: "
                "ollama, openai, anthropic, custom_openapi."
            )
            return result
        result.provider_kind = provider_kind

        # Budget check.
        budget = int(
            (integration.config or {}).get(
                "daily_call_budget", DEFAULT_DAILY_BUDGET
            )
        )
        used_today = await self._calls_today(integration.id)
        if used_today >= budget:
            result.budget_exceeded = True
            log.info(
                "ai.suggestions.budget_exceeded",
                integration_id=integration.id,
                used=used_today,
                budget=budget,
            )
            return result

        # Build the context payload + system + user messages.
        context = await self._build_context(integration)
        messages = [
            ChatMessage(role="system", content=SYSTEM_PROMPT),
            ChatMessage(
                role="user",
                content=(
                    "Here is the current Auditarr state. Propose new "
                    "rules per the constraints in the system prompt.\n\n"
                    + json.dumps(context, indent=2, sort_keys=True)
                ),
            ),
        ]

        # Build config + run the chat.
        config = await self._build_provider_config(
            integration, provider_kind
        )
        provider = get_ai_provider(provider_kind)

        try:
            chat = await provider.chat(config, messages)
        except Exception as exc:  # noqa: BLE001
            # v1.9 audit fix (AI-5): sanitize api_key-like
            # tokens from the error string before it lands in
            # the warning log and the audit row. Most provider
            # exceptions don't include secrets, but defensive
            # scrubbing protects against future provider
            # implementations that include request details in
            # their error messages.
            sanitized = _sanitize_error(str(exc))
            log.warning(
                "ai.suggestions.provider_failed",
                integration_id=integration.id,
                provider=provider_kind,
                error=sanitized,
            )
            result.error = f"Provider call failed: {sanitized}"
            await self._record_call_audit(
                integration.id, status="error", error=sanitized
            )
            return result

        proposals = _extract_proposals(chat.content)
        result.candidates_received = len(proposals)

        await self._record_call_audit(
            integration.id,
            status="ok",
            tokens_in=chat.prompt_tokens,
            tokens_out=chat.completion_tokens,
            candidates=len(proposals),
        )

        for proposal in proposals:
            try:
                definition = proposal.get("definition") or {}
                # Validate against the RuleDefinition pydantic
                # model — anything that doesn't parse cleanly
                # is rejected without writing.
                # v1.9 audit fix (AI-10): persist the validated /
                # normalized model_dump() rather than the raw
                # dict, so any future schema normalization flows
                # through to storage.
                parsed = RuleDefinition.model_validate(definition)
                definition = parsed.model_dump()
            except Exception as exc:  # noqa: BLE001
                log.info(
                    "ai.suggestions.invalid_proposal",
                    error=str(exc),
                )
                result.candidates_rejected += 1
                continue

            # v1.9 audit fix (AI-4): hard-reject proposals
            # containing destructive ``delete`` actions. The
            # system prompt forbids them but the LLM can still
            # emit one — operators reviewing a long list of
            # suggestions might miss a hidden ``delete`` action
            # inside a multi-action rule.
            if _contains_delete_action(definition):
                log.info(
                    "ai.suggestions.delete_rejected",
                    name=proposal.get("name"),
                )
                result.candidates_rejected += 1
                continue

            name = str(proposal.get("name") or "AI suggestion")
            evidence = {
                "rationale": str(proposal.get("rationale") or ""),
                "provider_kind": provider_kind,
                "provider_integration_id": integration.id,
                "model": chat.model,
            }
            # v1.9 audit fix (AI-3): build a content-hashed
            # dedup_key. The previous form ``ai:{kind}:{name}``
            # collided whenever the AI proposed the same name
            # twice across runs — guaranteed on re-invocation
            # because pending AI suggestions weren't surfaced in
            # the prompt context. The content hash makes
            # re-proposals of the SAME rule dedupe deterministically,
            # while two different rules with the same name
            # (rare) get distinct keys.
            dedup_key = _dedup_key_for_ai(
                provider_kind=provider_kind,
                name=name,
                definition=definition,
            )

            # Skip-if-existing rather than crash-on-insert. The
            # repository's get_by_dedup_key respects all statuses;
            # a re-proposal that matches a previously-DISMISSED
            # AI suggestion stays dismissed (we don't resurrect
            # rejected suggestions on re-run).
            from app.services.repositories import RuleSuggestionRepository

            repo = RuleSuggestionRepository(self._session)
            existing = await repo.get_by_dedup_key(dedup_key)
            if existing is not None:
                result.candidates_rejected += 1
                continue

            suggestion = RuleSuggestion(
                name=name,
                definition=definition,
                heuristic=f"ai_{provider_kind}",
                evidence=evidence,
                files_affected=0,
                est_runtime_s=None,
                confidence=0.5,
                dedup_key=dedup_key,
                status="pending",
            )
            self._session.add(suggestion)
            result.suggestions_created += 1

        await self._session.commit()
        log.info(
            "ai.suggestions.complete",
            integration_id=integration.id,
            provider=provider_kind,
            created=result.suggestions_created,
            rejected=result.candidates_rejected,
        )
        return result

    async def _resolve_provider(
        self, provider_integration_id: str | None
    ) -> Integration | None:
        if provider_integration_id:
            result = await self._session.execute(
                select(Integration).where(
                    Integration.id == provider_integration_id
                )
            )
            row = result.scalar_one_or_none()
            if row and row.enabled and row.kind == "ai-provider":
                return row
            return None
        result = await self._session.execute(
            select(Integration)
            .where(Integration.kind == "ai-provider")
            .where(Integration.enabled.is_(True))
            .order_by(Integration.created_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _calls_today(self, integration_id: str) -> int:
        cutoff = utcnow() - _dt.timedelta(hours=24)
        result = await self._session.execute(
            select(func.count())
            .select_from(AuditLogEntry)
            .where(AuditLogEntry.action == "ai.suggestions.call")
            .where(AuditLogEntry.target_id == integration_id)
            .where(AuditLogEntry.occurred_at >= cutoff)
        )
        return int(result.scalar_one() or 0)

    # v1.10 — public usage summary for the operator-facing
    # status surface. The budget check itself stays via
    # ``_calls_today`` (private; called inside the generator);
    # this method shapes the same number into a response model
    # the API can return without exposing internals.
    async def usage_summary(
        self, integration: Integration
    ) -> dict[str, Any]:
        used = await self._calls_today(integration.id)
        budget = int(
            (integration.config or {}).get(
                "daily_call_budget", DEFAULT_DAILY_BUDGET
            )
        )
        # Surface the next-reset time as an ISO timestamp.
        # Rolling 24h: the oldest in-window call's occurred_at +
        # 24h is when the budget will free up by one. For the
        # operator's purposes, "approx 24h from now" is fine.
        next_reset = (utcnow() + _dt.timedelta(hours=24)).isoformat()
        return {
            "integration_id": integration.id,
            "provider_kind": (
                (integration.config or {}).get("provider_kind") or "unknown"
            ),
            "calls_used_24h": used,
            "daily_call_budget": budget,
            "budget_remaining": max(0, budget - used),
            "budget_exceeded": used >= budget,
            "window_kind": "rolling_24h",
            "next_reset_at": next_reset,
        }

    async def _build_context(
        self, integration: Integration
    ) -> dict[str, Any]:
        # Active rules — name + definition. We send the
        # definition so the AI can avoid near-duplicates.
        rules = (
            (
                await self._session.execute(
                    select(Rule).where(Rule.enabled.is_(True))
                )
            )
            .scalars()
            .all()
        )
        active_rules = [
            {"name": r.name, "definition": r.definition} for r in rules
        ]

        # Recently-rejected (dismissed) suggestions. The
        # rejection list is the strongest signal the operator
        # cares about — "the AI keeps proposing this and I
        # keep saying no".
        cutoff = utcnow() - _dt.timedelta(days=60)
        dismissed = (
            (
                await self._session.execute(
                    select(RuleSuggestion)
                    .where(RuleSuggestion.status == "dismissed")
                    .where(RuleSuggestion.created_at >= cutoff)
                    .order_by(RuleSuggestion.created_at.desc())
                    .limit(25)
                )
            )
            .scalars()
            .all()
        )
        rejected = [
            {"name": s.name, "definition": s.definition} for s in dismissed
        ]

        # Device summary — top 5 devices by play count, with
        # their decision split.
        devices = (
            (
                await self._session.execute(
                    select(PlaybackDevice)
                    .order_by(PlaybackDevice.playback_count.desc())
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
        device_summary = [
            {
                "name": d.name or "(unnamed)",
                "platform": d.platform,
                "playback_count": d.playback_count,
                "transcode_count": d.transcode_count,
                "direct_play_count": d.direct_play_count,
            }
            for d in devices
        ]

        # Top transcoded files in the window — anonymize paths.
        send_paths = bool(
            (integration.config or {}).get("send_paths_external", True)
        )
        libraries = (
            (await self._session.execute(select(Library))).scalars().all()
        )
        # Build a longest-first list of (root, replacement)
        # tuples for path anonymization.
        path_subs = sorted(
            [(str(lib.root_path).rstrip("/"), "<library>") for lib in libraries],
            key=lambda kv: len(kv[0]),
            reverse=True,
        )

        cutoff_events = utcnow() - _dt.timedelta(days=30)
        # Group by source_path and take top-N by transcode count.
        from sqlalchemy import case as sql_case

        rows = await self._session.execute(
            select(
                PlaybackEvent.source_path,
                func.count().label("total"),
                func.sum(
                    sql_case(
                        (PlaybackEvent.decision == "transcode", 1),
                        else_=0,
                    )
                ).label("transcodes"),
            )
            .where(PlaybackEvent.started_at >= cutoff_events)
            .group_by(PlaybackEvent.source_path)
            .order_by(func.count().desc())
            .limit(TOP_FILES_LIMIT)
        )
        top_files: list[dict[str, Any]] = []
        for row in rows.all():
            path = str(row.source_path or "")
            if send_paths:
                anon = _anonymize_path(path, path_subs)
            else:
                anon = "<redacted>"
            top_files.append(
                {
                    "path": anon,
                    "total": int(row.total or 0),
                    "transcodes": int(row.transcodes or 0),
                }
            )

        # Library size summary — count by kind.
        from app.models.media import MediaFile

        size_rows = await self._session.execute(
            select(Library.kind, func.count(MediaFile.id))
            .select_from(MediaFile)
            .join(Library, MediaFile.library_id == Library.id)
            .group_by(Library.kind)
        )
        library_summary = {
            str(kind): int(count) for kind, count in size_rows.all()
        }

        # Vocabulary — fields + operators + actions per the
        # rule DSL. Keep this small so the prompt budget is
        # spent on the operator's data, not on schema we
        # could've put in the system prompt.
        vocabulary = {
            "fields": [
                "video_codec",
                "audio_codec",
                "container",
                "width",
                "height",
                "bitrate_kbps",
                "size_bytes",
                "has_subtitles",
                "filename",
                "path",
                "category",
                "library_id",
            ],
            "operators": ["eq", "ne", "lt", "le", "gt", "ge", "in", "nin"],
            "actions": [
                "set_severity",
                "add_tag",
                "queue_optimization",
                "notify",
                "vt_lookup",
                "search_upstream",
            ],
            "severities": ["ok", "info", "warn", "high", "error", "crit"],
        }

        return {
            "active_rules": active_rules,
            "rejected_suggestions": rejected,
            "devices": device_summary,
            "top_files": top_files,
            "library_summary": library_summary,
            "rule_dsl": vocabulary,
        }

    async def _build_provider_config(
        self, integration: Integration, provider_kind: str
    ) -> AIProviderConfig:
        secrets = {}
        if integration.secrets_ciphertext:
            try:
                secrets = get_secret_box().decrypt_dict(
                    integration.secrets_ciphertext
                )
            except Exception:  # noqa: BLE001
                secrets = {}
        cfg = integration.config or {}
        return AIProviderConfig(
            endpoint=str(cfg.get("endpoint") or ""),
            model=str(cfg.get("model") or ""),
            api_key=str(secrets.get("api_key") or "") or None,
            temperature=float(cfg.get("temperature", 0.2)),
            max_tokens=int(cfg.get("max_tokens", 1024)),
            daily_call_budget=int(
                cfg.get("daily_call_budget", DEFAULT_DAILY_BUDGET)
            ),
        )

    async def _record_call_audit(
        self,
        integration_id: str,
        *,
        status: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        candidates: int = 0,
        error: str | None = None,
    ) -> None:
        """Audit-log each call so the budget reader can count
        usage and the operator has a paper trail."""
        metadata: dict[str, Any] = {
            "status": status,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "candidates": candidates,
        }
        if error:
            metadata["error"] = error
        self._session.add(
            AuditLogEntry(
                action="ai.suggestions.call",
                actor_id=None,
                actor_label="ai_suggestion_service",
                target_type="integration",
                target_id=integration_id,
                metadata_=metadata,
            )
        )
        await self._session.flush()


# ── Helpers ────────────────────────────────────────────────────


def _anonymize_path(
    path: str, substitutions: list[tuple[str, str]]
) -> str:
    """Replace each known library root prefix with its
    placeholder. Longest-first iteration so a nested library
    like ``/mnt/media/Movies/4K`` rewrites before the parent
    ``/mnt/media/Movies``.

    Pure function, exposed for testing."""
    for root, placeholder in substitutions:
        if not root:
            continue
        if path.startswith(root + "/"):
            return placeholder + path[len(root):]
        if path == root:
            return placeholder
    return path


def _extract_proposals(text: str) -> list[dict[str, Any]]:
    """Pull the JSON array out of the AI's response. Tolerant
    of mild prose leakage (the constraints say "JSON only" but
    LLMs sometimes wrap in ```json fences anyway).

    Returns [] when nothing parses — the caller treats that as
    "no suggestions, no error"."""
    if not text:
        return []
    text = text.strip()
    # Strip fenced code blocks.
    fence_match = re.search(
        r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL
    )
    if fence_match:
        text = fence_match.group(1)
    # Find the first '[' and the last ']' as a fallback.
    if not text.startswith("["):
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def _dedup_key_for_ai(
    *, provider_kind: str, name: str, definition: dict[str, Any]
) -> str:
    """v1.9 audit fix (AI-3) — content-hashed dedup key.

    Hashes a canonical JSON encoding of the definition so two
    re-proposals of the same rule produce the same key (idempotent
    re-runs). Different rules with colliding names get distinct
    keys because the definition differs.

    The hash is truncated to 16 hex chars — collision probability
    at our scale (operator has hundreds of suggestions tops) is
    cosmologically small.
    """
    import hashlib

    canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]
    # Keep the provider_kind + name fragment so the dedup_key is
    # debuggable; the hash provides the actual uniqueness.
    safe_name = name[:64].replace(":", "_")
    return f"ai:{provider_kind}:{safe_name}:{digest}"


def _contains_delete_action(definition: dict[str, Any]) -> bool:
    """v1.9 audit fix (AI-4) — detect ``delete`` actions in any
    proposed RuleDefinition.

    The system prompt forbids them, but a hallucinating or
    jailbroken LLM can still emit one. Operators reviewing a
    long suggestion list might miss a delete buried in a
    multi-action rule. Hard reject here so a destructive action
    never reaches the review surface."""
    actions = definition.get("actions") if isinstance(definition, dict) else None
    if not isinstance(actions, list):
        return False
    for action in actions:
        if isinstance(action, dict) and str(action.get("type", "")).lower() == "delete":
            return True
    return False


def _sanitize_error(text: str) -> str:
    """v1.9 audit fix (AI-5) — strip credential-looking patterns
    from an error string before persisting it.

    Patterns matched:
      * ``Bearer <something>``        → ``Bearer <redacted>``
      * ``sk-<base62-ish>`` (OpenAI)   → ``sk-<redacted>``
      * ``Authorization: ...``         → ``Authorization: <redacted>``
      * ``api_key=<...>`` query param  → ``api_key=<redacted>``
      * ``x-api-key: <...>``           → ``x-api-key: <redacted>``

    Pure function; idempotent on already-redacted strings.
    """
    if not text:
        return text
    out = text
    out = re.sub(
        r"Bearer\s+[A-Za-z0-9._\-+/=]+",
        "Bearer <redacted>",
        out,
    )
    out = re.sub(
        r"\bsk-[A-Za-z0-9_\-]{8,}",
        "sk-<redacted>",
        out,
    )
    out = re.sub(
        r"(?i)Authorization:\s*[^\s,;]+",
        "Authorization: <redacted>",
        out,
    )
    out = re.sub(
        r"(?i)(api[-_]?key)[=:]\s*[A-Za-z0-9._\-+/=]+",
        r"\1=<redacted>",
        out,
    )
    out = re.sub(
        r"(?i)x-api-key:\s*[^\s,;]+",
        "x-api-key: <redacted>",
        out,
    )
    return out


__all__ = [
    "AISuggestionService",
    "AISuggestResult",
    "DEFAULT_DAILY_BUDGET",
    "SYSTEM_PROMPT",
    "_anonymize_path",
    "_contains_delete_action",
    "_dedup_key_for_ai",
    "_extract_proposals",
    "_sanitize_error",
]
