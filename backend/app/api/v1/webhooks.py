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

Stage 11 (v1.7, plan §540-546 + addendum B.8) — per-Integration
source whitelist. When ``Integration.config["source_whitelist"]``
contains one or more CIDR ranges or hostnames, requests from
addresses NOT in the list are rejected with 403 BEFORE signature
verification runs. Per addendum B.8 the whitelist is per-Integration
(per webhook endpoint), not per-channel — each Integration row IS
an endpoint, so an operator wanting two upstreams with different
whitelists configures two Integration rows.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import socket
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from app.api.dependencies import EventBusDep, RegistryDep, SessionDep
from app.core.logging import get_logger
from app.services.repositories import IntegrationRepository
from app.services.webhook_dispatcher import dispatch
from app.security.secrets import get_secret_box

log = get_logger("auditarr.webhooks.api", category="webhooks")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Integration kinds the receiver knows how to dispatch. The
# ``/integrations/{id}/webhook-secret`` rotate endpoint imports this
# set so it can refuse to mint a URL the receiver would just 404 on.
WEBHOOK_RECEIVER_KINDS: frozenset[str] = frozenset(
    {"sonarr", "radarr", "plex", "jellyfin"}
)
_SUPPORTED_KINDS = WEBHOOK_RECEIVER_KINDS


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

    # Stage 11 (plan §540-546 + addendum B.8) — per-Integration
    # source whitelist. When the integration's config carries a
    # non-empty ``source_whitelist``, the request source IP must
    # be in the list. Rejection is a 403 (auth-adjacent) so
    # upstreams know to stop retrying rather than waiting for a
    # 401 retry-with-credentials.
    #
    # We check whitelist BEFORE signature verification because:
    # (a) it's cheaper (no HMAC compute on rejected requests),
    # (b) it's the operator's first line of defence and shouldn't
    #     depend on a correctly-configured secret.
    whitelist_raw = (integration.config or {}).get("source_whitelist")
    if whitelist_raw:
        client_host = (
            request.client.host
            if request.client is not None
            else ""
        )
        if not _matches_source_whitelist(client_host, whitelist_raw):
            log.warning(
                "webhook.source_rejected",
                integration_id=integration_id,
                kind=kind,
                client_host=client_host,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Source not in this integration's whitelist."
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


# ── Stage 11 (v1.7) — source-whitelist matcher ──────────────────


def _matches_source_whitelist(
    client_host: str, whitelist: Any
) -> bool:
    """Check whether ``client_host`` matches any entry in the
    integration's whitelist.

    Entries may be:
      * An exact IPv4/IPv6 address (e.g. ``"192.168.1.10"``).
      * A CIDR range (e.g. ``"192.168.1.0/24"``, ``"::1/128"``).
      * A hostname (e.g. ``"sonarr.local"``). Hostnames are
        resolved at check time via :func:`socket.gethostbyname`
        — operators with DNS-driven whitelists pay the lookup
        cost per request but get the dynamic-IP-tolerant
        behaviour they're asking for.

    Returns False on:
      * Empty / non-list ``whitelist``.
      * Empty ``client_host``.
      * No entry matching.

    Hostname resolution errors are treated as a non-match for
    the entry (we don't fail the whole request on one bad
    hostname entry).
    """
    if not whitelist or not client_host:
        return False
    if not isinstance(whitelist, list):
        return False

    # Pre-parse the client address once — most entries we
    # compare against are IP/CIDR so we avoid re-parsing.
    try:
        client_ip = ipaddress.ip_address(client_host)
    except ValueError:
        client_ip = None

    for raw_entry in whitelist:
        entry = str(raw_entry or "").strip()
        if not entry:
            continue

        # CIDR range — contains "/".
        if "/" in entry:
            try:
                network = ipaddress.ip_network(entry, strict=False)
            except ValueError:
                continue
            if client_ip is not None and client_ip in network:
                return True
            continue

        # Exact IP — try to parse as one.
        try:
            entry_ip = ipaddress.ip_address(entry)
            if client_ip is not None and entry_ip == client_ip:
                return True
            continue
        except ValueError:
            pass  # entry isn't a literal IP; fall through to hostname resolution

        # Hostname — resolve to IP and compare. We use
        # ``getaddrinfo`` rather than ``gethostbyname`` so
        # IPv6 lookups work too.
        try:
            for info in socket.getaddrinfo(entry, None):
                resolved = info[4][0]
                try:
                    resolved_ip = ipaddress.ip_address(resolved)
                except ValueError:
                    continue
                if client_ip is not None and resolved_ip == client_ip:
                    return True
        except (socket.gaierror, UnicodeError, OSError):
            # Bad hostname or DNS unavailable — this entry
            # contributes nothing to the match decision, but
            # other entries may still match. Don't crash the
            # whole request on one bad entry.
            continue

    return False
