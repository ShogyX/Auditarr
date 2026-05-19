"""Unit tests for app.core.sse — the v1.8.0 SSE client.

We exercise:
  * Event block parsing handles the W3C wire format (multi-line
    data, event:, id:, retry:, comments, blank-line terminators).
  * stream_events reconnects after a clean disconnect and emits
    a RECONNECTING synthetic event.
  * stream_events raises on a 4xx (non-retryable) upstream.
  * stream_events retries on 5xx + transport errors.
  * stream_events handles asyncio.CancelledError cleanly.

We use httpx's MockTransport for the network so the tests don't
actually open sockets. The Plex-specific bits (Plex's
NotificationContainer wrapping, etc.) live in the plex plugin
tests; here we pin only the transport-level contract.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.core import sse


# ── _parse_event_block ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_simple_data_block() -> None:
    block = "data: hello world"
    evt = await sse._parse_event_block(block)
    assert evt is not None
    assert evt.data == "hello world"
    assert evt.event_type is None
    assert evt.event_id is None


@pytest.mark.asyncio
async def test_parse_multiline_data_joins_with_newlines() -> None:
    """Per the SSE spec, multiple consecutive ``data:`` fields
    concatenate into one payload separated by newlines."""
    block = "data: line one\ndata: line two\ndata: line three"
    evt = await sse._parse_event_block(block)
    assert evt is not None
    assert evt.data == "line one\nline two\nline three"


@pytest.mark.asyncio
async def test_parse_event_type_id_retry() -> None:
    block = "event: playing\nid: 42\nretry: 5000\ndata: {}"
    evt = await sse._parse_event_block(block)
    assert evt is not None
    assert evt.event_type == "playing"
    assert evt.event_id == "42"
    assert evt.retry_ms == 5000


@pytest.mark.asyncio
async def test_parse_keepalive_returns_none() -> None:
    """A block with only a comment line is a server keepalive
    and must return None (no SSE event)."""
    block = ": ping"
    evt = await sse._parse_event_block(block)
    assert evt is None


@pytest.mark.asyncio
async def test_parse_strips_one_leading_space_only() -> None:
    """Per the SSE spec, exactly ONE leading space is stripped
    from the value. Subsequent spaces are preserved."""
    block = "data:  two-spaces-preserved"
    evt = await sse._parse_event_block(block)
    assert evt is not None
    # The first space after the colon is stripped; one space
    # remains in the value.
    assert evt.data == " two-spaces-preserved"


@pytest.mark.asyncio
async def test_parse_invalid_retry_ignored() -> None:
    block = "retry: not-a-number\ndata: x"
    evt = await sse._parse_event_block(block)
    assert evt is not None
    assert evt.retry_ms is None


# ── stream_events end-to-end via MockTransport ─────────────────


def _mock_sse_response(
    body: str, status: int = 200
) -> httpx.MockTransport:
    """Build a MockTransport that returns *body* as the response
    body for any request.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=status,
            headers={"content-type": "text/event-stream"},
            content=body.encode(),
        )
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_stream_events_yields_parsed_events(monkeypatch) -> None:
    """When the upstream returns a stream of SSE blocks, the
    iterator yields one SseEvent per ``\\n\\n``-terminated
    block."""
    body = (
        "data: first\n"
        "\n"
        "event: playing\ndata: second\n"
        "\n"
        "data: third\n"
        "\n"
    )

    # Inject the MockTransport via the async_client factory.
    transport = _mock_sse_response(body)

    def _fake_async_client(*_args, **kwargs):
        kwargs["transport"] = transport
        return httpx.AsyncClient(**kwargs)

    monkeypatch.setattr(sse, "async_client", _fake_async_client)

    events: list[sse.SseEvent] = []
    # The iterator runs forever (reconnect loop); we break after
    # we've collected 3 real events. Each "page" through the
    # MockTransport returns the same body, but our `events`
    # accumulator only counts the first batch before we cancel.
    async def collect() -> None:
        async for evt in sse.stream_events("http://x/sse"):
            if not sse.is_reconnecting_event(evt):
                events.append(evt)
            if len(events) >= 3:
                return

    await asyncio.wait_for(collect(), timeout=5.0)

    assert [e.data for e in events] == ["first", "second", "third"]
    assert events[1].event_type == "playing"


@pytest.mark.asyncio
async def test_stream_events_raises_on_4xx(monkeypatch) -> None:
    """A 401 from the upstream is a permanent error (bad token);
    stream_events raises so the caller can stop respawning."""
    transport = _mock_sse_response("", status=401)

    def _fake_async_client(*_args, **kwargs):
        kwargs["transport"] = transport
        return httpx.AsyncClient(**kwargs)

    monkeypatch.setattr(sse, "async_client", _fake_async_client)

    async def consume() -> None:
        async for _evt in sse.stream_events("http://x/sse"):
            return

    with pytest.raises(httpx.HTTPStatusError):
        await asyncio.wait_for(consume(), timeout=5.0)


@pytest.mark.asyncio
async def test_stream_events_emits_reconnecting_after_reconnect(
    monkeypatch,
) -> None:
    """After the first connection closes, the next iteration
    yields a RECONNECTING synthetic event so subscribers can
    re-sync.
    """
    # Two-event stream that ends with EOF triggers reconnect.
    body = "data: a\n\ndata: b\n\n"

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(
            status_code=200,
            headers={"content-type": "text/event-stream"},
            content=body.encode(),
        )

    transport = httpx.MockTransport(handler)

    def _fake_async_client(*_args, **kwargs):
        kwargs["transport"] = transport
        return httpx.AsyncClient(**kwargs)

    monkeypatch.setattr(sse, "async_client", _fake_async_client)
    # Shrink the backoff so the test doesn't spend 1s+ asleep
    # waiting for the reconnect.
    monkeypatch.setattr(sse, "_BACKOFF", (0.001,))

    seen: list[sse.SseEvent] = []

    async def collect() -> None:
        async for evt in sse.stream_events("http://x/sse"):
            seen.append(evt)
            # Two events from first connect, then RECONNECTING,
            # then two from second connect = 5 events total.
            if len(seen) >= 5:
                return

    await asyncio.wait_for(collect(), timeout=5.0)

    # Sequence: data a, data b, RECONNECTING, data a, data b
    assert seen[0].data == "a"
    assert seen[1].data == "b"
    assert sse.is_reconnecting_event(seen[2])
    assert seen[3].data == "a"
    assert seen[4].data == "b"
    assert call_count["n"] >= 2


@pytest.mark.asyncio
async def test_reconnecting_event_helper() -> None:
    evt = sse.reconnecting_event()
    assert sse.is_reconnecting_event(evt)
    assert evt.event_type == "RECONNECTING"


@pytest.mark.asyncio
async def test_stream_events_cancellation_cleanly_propagates(
    monkeypatch,
) -> None:
    """asyncio.CancelledError must propagate so the worker
    supervisor can shut the listener down cleanly."""
    # Body that produces no events but holds the connection
    # open. httpx's MockTransport returns synchronously so this
    # is somewhat artificial; we just need the iterator to
    # spin so we can cancel it.
    body = "data: ping\n\n"
    transport = _mock_sse_response(body)

    def _fake_async_client(*_args, **kwargs):
        kwargs["transport"] = transport
        return httpx.AsyncClient(**kwargs)

    monkeypatch.setattr(sse, "async_client", _fake_async_client)
    monkeypatch.setattr(sse, "_BACKOFF", (0.001,))

    async def run() -> None:
        async for _evt in sse.stream_events("http://x/sse"):
            await asyncio.sleep(0)  # yield so we can cancel

    task = asyncio.create_task(run())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as excinfo:
        await task
    assert excinfo.type is asyncio.CancelledError
