"""v1.9 Stage 8.1 — in-memory log ring buffer.

Captures the most recent N log records (default 5000) so the
operator can inspect what's been happening from the UI without
needing shell access to ``/var/log/auditarr/``. The buffer is a
fixed-size deque; once it's full, oldest entries are evicted
on each new push.

Records carry:
  * ``timestamp`` — UTC ISO string.
  * ``level``     — "debug" | "info" | "warning" | "error" | "critical".
  * ``logger``    — structlog logger name (e.g. ``auditarr.api``).
  * ``category``  — the structlog ``category`` bind, when present.
                    Stage 8.1's API uses this as the ``service``
                    facet (``api``, ``worker``, ``scheduler``, ...).
  * ``event``     — the structlog event string (i.e. the log
                    message identifier).
  * ``context``   — dict of any extra structlog bindings (e.g.
                    ``integration_id``, ``request_id``,
                    ``user_id``). We DO NOT include the
                    full record dict — secrets / tokens / API
                    keys may end up in bindings, and serving
                    them via an authenticated UI is still a
                    larger attack surface than the operator
                    expects. We allow-list the keys we know
                    are safe (see ``_SAFE_CONTEXT_KEYS``).

The buffer is a singleton accessed via ``get_log_buffer()``.
``LogCaptureHandler`` is the stdlib-logging adapter that pushes
records into the buffer; install it once during
``configure_logging``.
"""

from __future__ import annotations

import datetime as _dt
import logging
import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

# v1.9 Stage 8.1 — keys we copy out of the structlog event dict
# into ``context``. We deliberately enumerate them rather than
# pass through everything: a log line built from operator-
# supplied data could otherwise leak secrets that ended up in a
# bound key by accident. Add to this list as new safe keys
# emerge in the codebase; don't open the door wider than that.
_SAFE_CONTEXT_KEYS: frozenset[str] = frozenset(
    {
        "integration_id",
        "integration_kind",
        "media_file_id",
        "media_file_path",
        "user_id",
        "request_id",
        "rule_id",
        "rule_name",
        "library_id",
        "library_name",
        "job_id",
        "upstream_id",
        "upstream_job_id",
        "target_id",
        "target_type",
        "session_id",
        "playback_id",
        "scanner_run_id",
        "duration_ms",
        "latency_ms",
        "elapsed_seconds",
        "fetched",
        "inserted",
        "resolved",
        "removed",
        "skipped",
        "count",
        "size",
        "status",
        "kind",
        "source",
        "action",
        "decision",
        "error",  # error message strings only; structured exception data is dropped
        "detail",
        "reason",
        "code",
        "method",
        "path",
        "endpoint",
    }
)


@dataclass(slots=True)
class LogRecord:
    """One captured log record."""

    timestamp: str
    level: str
    logger: str
    category: str | None
    event: str
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LogRingBuffer:
    """Thread-safe fixed-size ring buffer for log records.

    The buffer accepts pushes from any thread / event loop —
    structlog logging happens from many places. Reads are
    one-shot snapshots so the API endpoint can serialize them
    without holding the lock for the whole serialization.
    """

    def __init__(self, capacity: int = 5000) -> None:
        if capacity <= 0:
            raise ValueError("LogRingBuffer capacity must be positive")
        self._capacity = capacity
        self._records: deque[LogRecord] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        # v1.9 Stage 8.1 — error-pulse signal for the sidebar's
        # red-dot indicator. Stores the timestamp (UTC) of the
        # most recent error/critical record. Read via
        # ``last_error_at``; the API surfaces it as a derived
        # "recent error" boolean.
        self._last_error_at: _dt.datetime | None = None

    @property
    def capacity(self) -> int:
        return self._capacity

    def push(self, record: LogRecord) -> None:
        with self._lock:
            self._records.append(record)
            if record.level in ("error", "critical"):
                try:
                    self._last_error_at = _dt.datetime.fromisoformat(
                        record.timestamp
                    )
                except ValueError:
                    self._last_error_at = _dt.datetime.now(_dt.UTC)

    def snapshot(self) -> list[LogRecord]:
        """Return a list copy of the current buffer contents
        (oldest first). Callers may filter / paginate the
        result without further locking."""
        with self._lock:
            return list(self._records)

    @property
    def last_error_at(self) -> _dt.datetime | None:
        with self._lock:
            return self._last_error_at

    def clear(self) -> None:
        """Drop every record. Used by tests to isolate fixtures."""
        with self._lock:
            self._records.clear()
            self._last_error_at = None


# Module-level singleton. Wrap in a getter so tests can swap
# instances via monkeypatch.
_global_buffer: LogRingBuffer | None = None


def get_log_buffer() -> LogRingBuffer:
    global _global_buffer
    if _global_buffer is None:
        _global_buffer = LogRingBuffer(capacity=5000)
    return _global_buffer


def set_log_buffer(buffer: LogRingBuffer) -> None:
    """Test hook — replace the singleton with a fresh buffer."""
    global _global_buffer
    _global_buffer = buffer


# ── stdlib logging handler ───────────────────────────────────────


class LogCaptureHandler(logging.Handler):
    """stdlib Handler that converts each record into a
    ``LogRecord`` and pushes it into the ring buffer.

    Structlog routes its events through stdlib logging via
    ``ProcessorFormatter.wrap_for_formatter``, which sets the
    raw structlog event dict as ``record.msg`` (a dict). We
    inspect that dict to extract category + context. Plain
    stdlib log calls (e.g. ``logging.info("hello")``) end up
    with ``record.msg`` as a string; we still capture those
    but without structured context.
    """

    def __init__(self, buffer: LogRingBuffer | None = None) -> None:
        super().__init__(level=logging.DEBUG)
        self._buffer = buffer

    @property
    def buffer(self) -> LogRingBuffer:
        # Lazy resolve so a buffer set after handler install is
        # picked up.
        return self._buffer or get_log_buffer()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = _record_to_log_record(record)
        except Exception:  # noqa: BLE001
            # Last-resort: never let a logging-handler failure
            # bring down the process. Drop the record silently
            # rather than reentering logging.
            return
        self.buffer.push(entry)


def _record_to_log_record(record: logging.LogRecord) -> LogRecord:
    """Convert a stdlib LogRecord into the buffer's LogRecord."""
    timestamp = _dt.datetime.fromtimestamp(
        record.created, tz=_dt.UTC
    ).isoformat()
    level = record.levelname.lower()
    logger_name = record.name or ""

    # Structlog hands us a dict in record.msg via wrap_for_formatter.
    # When that's the case, we can extract the event + context.
    msg = record.msg
    if isinstance(msg, dict):
        event = str(msg.get("event") or "")
        category = msg.get("category")
        context = _safe_context(msg)
    else:
        event = str(msg) if msg is not None else ""
        category = None
        context = {}

    return LogRecord(
        timestamp=timestamp,
        level=level,
        logger=logger_name,
        category=str(category) if category is not None else None,
        event=event,
        context=context,
    )


def _safe_context(event_dict: dict[str, Any]) -> dict[str, Any]:
    """Project the structlog event dict onto the allow-listed
    context keys. Drops anything outside the allow-list."""
    out: dict[str, Any] = {}
    for key in _SAFE_CONTEXT_KEYS:
        if key in event_dict and event_dict[key] is not None:
            value = event_dict[key]
            # Coerce non-trivial types to strings to keep the
            # JSON payload predictable.
            if isinstance(value, (str, int, float, bool)):
                out[key] = value
            else:
                out[key] = str(value)
    return out


# ── File-tailing fallback ────────────────────────────────────────


def tail_log_file(
    path: str, *, max_records: int = 200
) -> Iterable[LogRecord]:
    """v1.9 Stage 8.1 — fallback path-tailer for
    ``/var/log/auditarr/<service>.log`` style installs.

    Yields LogRecord rows reconstructed from the file. We
    don't merge file rows with the ring buffer in the API
    handler — too much room for ordering bugs. The API picks
    one source (buffer by default, file when ``source=file``).
    """
    import json

    try:
        with open(path, encoding="utf-8") as fp:
            lines = fp.readlines()[-max_records:]
    except FileNotFoundError:
        return []
    except OSError:
        return []

    out: list[LogRecord] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # JSON-format renderer first; fall back to plain text.
        try:
            blob = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            out.append(
                LogRecord(
                    timestamp=_dt.datetime.now(_dt.UTC).isoformat(),
                    level="info",
                    logger="file",
                    category=None,
                    event=line,
                    context={},
                )
            )
            continue
        if not isinstance(blob, dict):
            continue
        out.append(
            LogRecord(
                timestamp=str(blob.get("timestamp") or ""),
                level=str(blob.get("level") or "info").lower(),
                logger=str(blob.get("logger") or ""),
                category=(
                    str(blob.get("category"))
                    if blob.get("category") is not None
                    else None
                ),
                event=str(blob.get("event") or ""),
                context=_safe_context(blob),
            )
        )
    return out


__all__ = [
    "LogCaptureHandler",
    "LogRecord",
    "LogRingBuffer",
    "get_log_buffer",
    "set_log_buffer",
    "tail_log_file",
]
