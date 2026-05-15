"""Webhook receivers (Stage 19 audit follow-up).

Endpoint:

  POST /api/v1/webhooks/{kind}/{integration_id}

Per-integration HMAC-SHA256 verification via the ``X-Auditarr-Signature``
header. Missing secret on the integration → 401. Mismatched signature
→ 401. Unknown kind → 404. Unknown integration → 404. Anything past
auth returns 200 regardless of whether the dispatcher took action;
this prevents upstreams from retrying on quirks they can't fix
(unrecognized event type, unmapped path, etc.).

The body is read raw (``await request.body()``) so signature
verification operates on the exact bytes the upstream signed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.api.dependencies import EventBusDep, RegistryDep, SessionDep
from app.core.logging import get_logger
from app.services.repositories import IntegrationRepository
from app.services.webhook_dispatcher import dispatch
from app.security.secrets import get_secret_box

log = get_logger("auditarr.webhooks.api", category="webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_SUPPORTED_KINDS = {"sonarr", "radarr", "plex", "jellyfin"}


@router.post(
    "/{kind}/{integration_id}",
    summary="Receive a webhook from an upstream integration",
)
async def receive_webhook(
    kind: str,
    integration_id: str,
    request: Request,
    session: SessionDep,
    registry: RegistryDep,
    bus: EventBusDep,
) -> dict[str, Any]:
    if kind not in _SUPPORTED_KINDS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unsupported webhook kind {kind!r}",
        )

    integration = await IntegrationRepository(session).get(integration_id)
    if integration is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration not found",
        )

    if integration.kind != kind:
        # The URL says "this is a sonarr webhook" but the integration
        # is a Radarr. Refuse — payload extractors are per-kind and
        # would misparse.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Integration {integration_id!r} is of kind "
                f"{integration.kind!r}, not {kind!r}"
            ),
        )

    if not integration.webhook_secret_ciphertext:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "No webhook secret configured for this integration. "
                "Generate one via "
                "POST /integrations/{id}/webhook-secret first."
            ),
        )

    raw_body = await request.body()
    secret = _decrypt_secret(integration.webhook_secret_ciphertext)
    if not _verify_signature(raw_body, secret, request.headers):
        log.warning(
            "webhook.signature_invalid",
            integration_id=integration_id,
            kind=kind,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing signature",
        )

    try:
        payload = json.loads(raw_body or b"{}")
    except json.JSONDecodeError:
        # Sonarr / Radarr ``Test`` events send a tiny body; accept
        # empty/invalid as a 200 so they don't retry. The dispatcher
        # will surface "ignored" in the response.
        payload = {}

    outcome = await dispatch(
        kind=kind,
        payload=payload,
        integration=integration,
        session=session,
        ctx={
            "bus": bus,
            "registry": registry,
        },
    )
    await session.commit()
    return {
        "kind": outcome.kind,
        "event": outcome.event,
        "action": outcome.action,
        "paths": outcome.paths,
        "detail": outcome.detail,
    }


def _decrypt_secret(ciphertext: str) -> str:
    """Decrypt the per-integration webhook secret. The plaintext is
    a short hex string set by ``POST /integrations/{id}/webhook-secret``;
    we serialize it inside a JSON ``{"value": ...}`` dict to reuse
    the existing ``SecretBox.encrypt_dict`` shape used for
    integration secrets."""
    box = get_secret_box()
    blob = box.decrypt_dict(ciphertext)
    return str(blob.get("value", ""))


def _verify_signature(
    raw_body: bytes, secret: str, headers: Any
) -> bool:
    """Compare ``X-Auditarr-Signature`` against
    HMAC-SHA256(secret, body). The expected header value is
    ``sha256=<hex>``; we accept either ``sha256=<hex>`` or the
    bare hex (some upstreams strip the prefix). Constant-time
    comparison via :func:`hmac.compare_digest` to avoid leaking
    bytes via timing differences."""
    sig_header = headers.get("x-auditarr-signature") or ""
    if not sig_header or not secret:
        return False
    received = sig_header.strip()
    if received.startswith("sha256="):
        received = received[len("sha256="):]
    expected = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(received, expected)
