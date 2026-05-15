"""WebSocket entrypoint.

Clients connect to ``/api/v1/ws?token=<jwt>&topics=media.,scan.`` to
receive a filtered event stream. The server fans :class:`DomainEvent`
instances published on the in-process bus to every matching connection.

Stage 14: enforce JWT auth on the upgrade. Browsers can't attach
``Authorization`` headers to a WebSocket handshake, so the token is
passed as a query parameter. We validate it through the same
:class:`TokenService` the HTTP endpoints use, and reject the upgrade
with a 1008 close code if it fails.

This closes a Stage 1 gap where any process on the same network as
Auditarr could subscribe to the event firehose and read internal
state — file paths, integration health detail, rule names — without
ever logging in. The behaviour is gated by ``settings.ws_require_auth``
(default True). Tests that don't care about WS auth can set the env
``AUDITARR_WS_REQUIRE_AUTH=false`` and skip the token entirely.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.api.websocket import get_ws_manager
from app.core.exceptions import AuthenticationError
from app.core.logging import get_logger
from app.core.settings import get_settings
from app.security.tokens import ACCESS, TokenService

router = APIRouter(tags=["websocket"])
log = get_logger("auditarr.ws", category="websocket")


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(
        "", description="Access JWT. Required unless ws_require_auth=False."
    ),
    topics: str = Query(
        "", description="Comma-separated topic prefixes; empty = all"
    ),
) -> None:
    settings = get_settings()

    if settings.ws_require_auth:
        if not token:
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION, reason="missing token"
            )
            log.info("ws.rejected", reason="missing_token")
            return
        try:
            claims = TokenService(settings).decode(token, expected_type=ACCESS)
        except AuthenticationError as exc:
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION, reason="invalid token"
            )
            log.info("ws.rejected", reason="invalid_token", detail=str(exc))
            return
        log.info("ws.authenticated", subject=claims.subject)

    topic_set = {t.strip() for t in topics.split(",") if t.strip()}
    manager = get_ws_manager()
    conn = await manager.connect(websocket, topic_set)
    try:
        while True:
            # Keep the socket open; inbound messages are reserved for future use.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(conn)
