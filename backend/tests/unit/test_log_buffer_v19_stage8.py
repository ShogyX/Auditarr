"""v1.9 Stage 8.1 — log ring buffer unit tests.

Pins:
  1. Buffer evicts oldest entries when capacity is exceeded.
  2. push/snapshot/clear are thread-safe (push from multiple
     threads concurrently does not corrupt the deque).
  3. ``last_error_at`` is updated only on error/critical
     records, not info/warning.
  4. ``_safe_context`` allow-lists known keys and drops the rest
     (no secrets surfacing).
  5. ``tail_log_file`` handles missing file gracefully.
  6. ``tail_log_file`` parses JSON-format records correctly.
  7. ``tail_log_file`` falls back to plain text for non-JSON
     lines.
  8. ``LogCaptureHandler.emit`` doesn't reenter logging on
     internal failure (defensive — passing a malformed record
     can't crash the app).
"""

from __future__ import annotations

import datetime as _dt
import logging
import threading

import pytest

from app.core.log_buffer import (
    LogCaptureHandler,
    LogRecord,
    LogRingBuffer,
    _safe_context,
    tail_log_file,
)


def _record(
    *,
    level: str = "info",
    event: str = "msg",
    category: str | None = None,
) -> LogRecord:
    return LogRecord(
        timestamp=_dt.datetime.now(_dt.UTC).isoformat(),
        level=level,
        logger="t",
        category=category,
        event=event,
        context={},
    )


def test_buffer_evicts_oldest_when_full() -> None:
    buf = LogRingBuffer(capacity=3)
    for i in range(5):
        buf.push(_record(event=f"e{i}"))
    snapshot = buf.snapshot()
    # Only the last 3 remain; oldest first.
    assert [r.event for r in snapshot] == ["e2", "e3", "e4"]


def test_buffer_raises_on_non_positive_capacity() -> None:
    with pytest.raises(ValueError):
        LogRingBuffer(capacity=0)
    with pytest.raises(ValueError):
        LogRingBuffer(capacity=-5)


def test_buffer_is_thread_safe_under_concurrent_pushes() -> None:
    """A handful of threads pushing concurrently should land
    exactly N records in the buffer — no corruption, no
    deadlock."""
    buf = LogRingBuffer(capacity=10_000)
    per_thread = 200
    threads_n = 8

    def worker(tid: int) -> None:
        for i in range(per_thread):
            buf.push(_record(event=f"t{tid}-{i}"))

    threads = [
        threading.Thread(target=worker, args=(t,)) for t in range(threads_n)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(buf.snapshot()) == per_thread * threads_n


def test_last_error_at_only_updates_on_error_or_critical() -> None:
    buf = LogRingBuffer(capacity=10)
    assert buf.last_error_at is None
    buf.push(_record(level="info"))
    buf.push(_record(level="warning"))
    assert buf.last_error_at is None
    buf.push(_record(level="error"))
    assert buf.last_error_at is not None
    first_error = buf.last_error_at
    buf.push(_record(level="critical"))
    assert buf.last_error_at >= first_error


def test_safe_context_drops_secrets() -> None:
    """Unknown keys (token, api_key, password, etc.) MUST NOT
    appear in the projected context. This is the allow-list
    contract that protects the API from surfacing sensitive
    fields that ended up in a log binding."""
    raw = {
        "event": "msg",
        # Allow-listed keys.
        "integration_id": "i-1",
        "user_id": "u-1",
        "request_id": "r-1",
        # NOT allow-listed.
        "token": "SECRET",
        "api_key": "SECRET",
        "password": "SECRET",
        "authorization": "Bearer xyz",
    }
    ctx = _safe_context(raw)
    assert ctx == {
        "integration_id": "i-1",
        "user_id": "u-1",
        "request_id": "r-1",
    }


def test_safe_context_coerces_non_primitive_types_to_string() -> None:
    raw = {
        "integration_id": ["i-1", "i-2"],  # list — coerced
        "duration_ms": 42.5,  # float — kept as-is
    }
    ctx = _safe_context(raw)
    assert ctx["integration_id"] == "['i-1', 'i-2']"
    assert ctx["duration_ms"] == 42.5


def test_clear_empties_buffer_and_last_error() -> None:
    buf = LogRingBuffer(capacity=5)
    buf.push(_record(level="error"))
    assert buf.last_error_at is not None
    buf.clear()
    assert buf.snapshot() == []
    assert buf.last_error_at is None


# ── File-tailing fallback ────────────────────────────────────────


def test_tail_log_file_missing_file_returns_empty(tmp_path) -> None:
    out = list(tail_log_file(str(tmp_path / "nonexistent.log")))
    assert out == []


def test_tail_log_file_parses_json_lines(tmp_path) -> None:
    path = tmp_path / "service.log"
    path.write_text(
        '{"timestamp": "2026-05-18T10:00:00+00:00", "level": "info", '
        '"logger": "auditarr.api", "category": "api", '
        '"event": "served", "request_id": "r-1"}\n'
        '{"timestamp": "2026-05-18T10:00:01+00:00", "level": "error", '
        '"logger": "auditarr.worker", "category": "worker", '
        '"event": "boom"}\n',
        encoding="utf-8",
    )
    records = list(tail_log_file(str(path)))
    assert len(records) == 2
    assert records[0].level == "info"
    assert records[0].category == "api"
    assert records[0].event == "served"
    assert records[0].context.get("request_id") == "r-1"
    assert records[1].level == "error"


def test_tail_log_file_falls_back_to_plain_text(tmp_path) -> None:
    path = tmp_path / "service.log"
    path.write_text("ordinary text line\nanother line\n", encoding="utf-8")
    records = list(tail_log_file(str(path)))
    assert len(records) == 2
    assert records[0].event == "ordinary text line"
    assert records[0].logger == "file"


def test_tail_log_file_caps_to_max_records(tmp_path) -> None:
    path = tmp_path / "service.log"
    path.write_text("\n".join(f"line-{i}" for i in range(500)), encoding="utf-8")
    records = list(tail_log_file(str(path), max_records=50))
    assert len(records) == 50
    # The tail behavior: latest 50 lines.
    assert records[-1].event == "line-499"


# ── Handler defensive behavior ───────────────────────────────────


def test_capture_handler_swallows_internal_failures() -> None:
    """A logging.Handler that raises during emit can cascade
    into the calling code's exception path. LogCaptureHandler
    catches any internal failure and drops the record silently
    so a broken handler can't crash the app."""
    buf = LogRingBuffer(capacity=10)
    handler = LogCaptureHandler(buffer=buf)

    # Craft a record whose msg is a non-dict, non-string sentinel
    # that triggers an attribute-access during _record_to_log_record
    # if the code path were to over-introspect. The current path
    # is safe by construction; this test pins that property.
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname="x",
        lineno=1,
        msg=object(),  # not dict, not string
        args=None,
        exc_info=None,
    )
    handler.emit(record)
    # Should have captured one record (msg coerced via str()).
    snap = buf.snapshot()
    assert len(snap) == 1
