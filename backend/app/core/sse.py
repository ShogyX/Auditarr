"""Async Server-Sent Events client with reconnect + backoff.

Used by the v1.8.0 Plex live-event listener and reusable for any
provider that exposes an SSE endpoint (Jellyfin v10.9+ does too,
though we currently target Plex specifically).

Why a custom implementation rather than ``httpx-sse`` or
``aiohttp-sse-client``? Two reasons:

1. We want explicit control over reconnect semantics. Plex's
   ``/:/eventsource/notifications`` drops the connection on
   server restart, network blips, and after long idle periods.
   The caller needs to know WHY the connection dropped so
   reconnection can be paced (immediate for clean drops,
   exponential backoff for upstream-down).

2. The SSE spec includes ``Last-Event-ID`` resume semantics
   that Plex doesn't honour. We DO want to track event IDs
   for our own dedup (some events repeat on reconnect), but
   we DON'T want to feed them back to Plex as Last-Event-ID
   because Plex will then return nothing.

The client yields :class:`SseEvent` instances as soon as each
``\\n\\n``-delimited block arrives. ``event_id``, ``event_type``,
and ``data`` are the standard SSE fields. ``data`` is the raw
text — callers parse JSON / NDJSON themselves since Plex's
``data`` payload is a JSON object but other servers use plain
strings.

Resilience contract:

  * On clean disconnect (200 → EOF) → emit a synthetic
    ``RECONNECTING`` event, sleep, retry. Plex closes idle
    connections after ~5 minutes; this is expected.
  * On 4xx upstream error → raise. Indicates auth or path is
    broken; caller shouldn't retry forever.
  * On 5xx / connection error → exponential backoff, retry.
    Caller can break out of the iteration if needed.
  * On asyncio.CancelledError → cleanly close the stream and
    re-raise. Allows graceful shutdown.

Backoff schedule: 1s, 2s, 4s, 8s, 16s, 30s, then 30s indefinitely.
Each successful event resets the counter.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Final

import httpx

from app.core.http import async_client
from app.core.logging import get_logger

log = get_logger("auditarr.sse", category="system")


# Backoff schedule in seconds. After exhausting the list we hold
# steady at the last value.
_BACKOFF: Final[tuple[float, ...]] = (1.0, 2.0, 4.0, 8.0, 16.0, 30.0)


@dataclass(slots=True)
class SseEvent:
    """One parsed SSE event.

    Fields follow the W3C spec
    (https://html.spec.whatwg.org/multipage/server-sent-events.html).
    All are optional except ``data`` which is the only field SSE
    guarantees on every message.

    ``RECONNECTING`` is a synthetic event_type we emit when the
    transport reconnects so subscribers can flush in-memory state
    and re-sync with a snapshot endpoint.
    """

    data: str
    event_type: str | None = None
    event_id: str | None = None
    retry_ms: int | None = None


_RECONNECTING_MARKER: Final[str] = "__auditarr_reconnecting__"


def reconnecting_event() -> SseEvent:
    """Synthetic event the stream emits before each reconnect attempt
    after the first successful connection. Subscribers should treat
    this as "your in-memory cache is stale; re-sync from a snapshot
    endpoint before trusting future events."
    """
    return SseEvent(data=_RECONNECTING_MARKER, event_type="RECONNECTING")


def is_reconnecting_event(evt: SseEvent) -> bool:
    return evt.event_type == "RECONNECTING"


async def _parse_event_block(block: str) -> SseEvent | None:
    """Parse one ``\\n\\n``-delimited SSE event block into an
    :class:`SseEvent`. Returns None for keepalive / comment blocks
    that contain only blank fields.
    """
    data_lines: list[str] = []
    event_type: str | None = None
    event_id: str | None = None
    retry_ms: int | None = None

    for raw_line in block.splitlines():
        if not raw_line or raw_line.startswith(":"):
            # Empty line or comment — skip. The empty line is what
            # terminates an event block; comments are keepalive.
            continue
        # Each line is ``field: value`` or just ``field`` (rare).
        if ":" in raw_line:
            field, _, value = raw_line.partition(":")
            # Per spec, strip ONE leading space from the value.
            if value.startswith(" "):
                value = value[1:]
        else:
            field, value = raw_line, ""

        if field == "data":
            data_lines.append(value)
        elif field == "event":
            event_type = value
        elif field == "id":
            event_id = value
        elif field == "retry":
            try:
                retry_ms = int(value)
            except ValueError:
                pass

    if not data_lines:
        return None
    return SseEvent(
        data="\n".join(data_lines),
        event_type=event_type,
        event_id=event_id,
        retry_ms=retry_ms,
    )


async def stream_events(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    verify: bool | str | None = None,
    timeout: float | None = None,
) -> AsyncIterator[SseEvent]:
    """Async iterator over SSE events from *url*.

    Reconnects automatically on transient failure. Emits a synthetic
    ``RECONNECTING`` event before each reconnect attempt (after the
    first connection) so subscribers can re-sync.

    Args:
        url: Full SSE endpoint URL.
        headers: Optional headers to send on every (re)connection.
            Typically the Plex token or Authorization header.
        verify: TLS verify behaviour. None → use the standard
            ``app.core.http.async_client`` default (resolves the
            host CA bundle). Pass False only for known-insecure
            local Plex deployments.
        timeout: Per-request timeout in seconds. None → no
            timeout (SSE streams are long-lived; we set
            ``read=None`` on the request).

    Raises:
        httpx.HTTPStatusError: On 4xx upstream errors that won't
            recover with a retry (401 invalid token, 404 wrong
            URL). 5xx are caught and retried.

    Yields:
        :class:`SseEvent` instances. Subscribers should check
        :func:`is_reconnecting_event` and re-sync state if True.
    """
    headers = dict(headers or {})
    # ``Accept: text/event-stream`` is the SSE spec signal. Plex
    # respects it; without it, some PMS builds return a static
    # HTML page from this URL.
    headers.setdefault("Accept", "text/event-stream")
    # ``Cache-Control: no-cache`` discourages intermediate caches
    # from buffering.
    headers.setdefault("Cache-Control", "no-cache")

    backoff_idx = 0
    first_connection = True

    while True:
        try:
            # httpx-specific: ``read=None`` allows the read to block
            # indefinitely between events. The connect timeout is
            # left at the caller's value (or httpx's default of 5s).
            client_timeout = httpx.Timeout(
                connect=timeout if timeout is not None else 10.0,
                read=None,
                write=10.0,
                pool=10.0,
            )
            client_kwargs: dict = {
                "timeout": client_timeout,
                "headers": headers,
            }
            if verify is not None:
                client_kwargs["verify"] = verify

            async with async_client(**client_kwargs) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code >= 400:
                        # 4xx → don't retry. Caller has an auth /
                        # config bug that won't fix itself.
                        if 400 <= response.status_code < 500:
                            response.raise_for_status()
                        # 5xx → retry with backoff. Drop into the
                        # exception handler below.
                        raise httpx.HTTPStatusError(
                            f"SSE upstream returned {response.status_code}",
                            request=response.request,
                            response=response,
                        )

                    if not first_connection:
                        # Tell subscribers we're back; they should
                        # re-sync any in-memory state against a
                        # snapshot endpoint before trusting future
                        # events (a session may have started AND
                        # ended during our disconnect).
                        yield reconnecting_event()
                    first_connection = False
                    backoff_idx = 0

                    log.info(
                        "sse.connected",
                        url=url,
                        status=response.status_code,
                    )

                    # Iterate the response body. httpx splits at
                    # line boundaries; we re-aggregate into
                    # ``\\n\\n``-terminated event blocks.
                    block_buf: list[str] = []
                    async for line in response.aiter_lines():
                        if line:
                            block_buf.append(line)
                        else:
                            # Empty line terminates the block.
                            block_text = "\n".join(block_buf)
                            block_buf.clear()
                            if not block_text:
                                continue
                            event = await _parse_event_block(block_text)
                            if event is not None:
                                yield event

                    # End-of-stream without error — Plex closed
                    # the connection. Fall through to reconnect.
                    log.info("sse.disconnected_clean", url=url)

        except asyncio.CancelledError:
            log.info("sse.cancelled", url=url)
            raise
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if 400 <= status < 500:
                # Permanent — don't retry.
                log.error(
                    "sse.fatal_upstream_error",
                    url=url,
                    status=status,
                    detail=(
                        "SSE upstream returned a non-retryable error. "
                        "Common causes: invalid token (401), endpoint "
                        "renamed (404), token lacks permission (403). "
                        "Listener will exit."
                    ),
                )
                raise
            # 5xx → fall through to backoff.
            log.warning(
                "sse.transient_upstream_error",
                url=url,
                status=status,
                backoff_idx=backoff_idx,
            )
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
            log.warning(
                "sse.transport_error",
                url=url,
                error=str(exc),
                error_type=type(exc).__name__,
                backoff_idx=backoff_idx,
            )
        except Exception as exc:  # noqa: BLE001
            # Defensive: don't crash the listener task on an
            # unexpected exception. Log loudly so the operator
            # can investigate.
            log.error(
                "sse.unexpected_error",
                url=url,
                error=str(exc),
                error_type=type(exc).__name__,
                backoff_idx=backoff_idx,
            )

        # Sleep before reconnect.
        sleep_s = _BACKOFF[min(backoff_idx, len(_BACKOFF) - 1)]
        backoff_idx += 1
        await asyncio.sleep(sleep_s)
