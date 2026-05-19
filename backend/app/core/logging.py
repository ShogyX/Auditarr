"""Structured logging configuration."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from app.core.settings import Settings

# Reusable category constants — every log call should set ``category``.
# Kept in sync with the frontend's LogsPage SERVICE_OPTIONS list. When
# adding a category here, also add it to `frontend/src/features/system/
# LogsPage.tsx` so operators can filter by it.
LOG_CATEGORIES = (
    "api",
    "scanner",
    "automation",
    "integrations",
    "updater",
    "notifications",
    "security",
    "database",
    "queue",
    "plugin",
    "events",
    "system",
    "playback",
    "rules",
)

# v1.9.x — shared log file path. Each process (gunicorn workers + arq
# worker) appends to this single file via O_APPEND; the API's
# /system/logs endpoint tails it so log records from every process
# are visible regardless of which gunicorn worker handles the
# request. The default lines up with /var/log/auditarr/ which the
# bare-metal installer creates owned by the auditarr service user.
_DEFAULT_SHARED_LOG_PATH = Path("/var/log/auditarr/auditarr.log")

# Self-truncate threshold. logrotate isn't guaranteed to be set up on
# every install, so guard against unbounded growth by truncating the
# file on process startup when it exceeds this size. The in-memory
# ring buffer is the live cap during the process's lifetime; the
# file is best-effort durable across processes/restarts. 50MB ≈
# 250k records at typical record size — plenty for incident triage,
# small enough to not eat the disk on a low-volume host.
_LOG_FILE_TRUNCATE_BYTES = 50 * 1024 * 1024


# v1.9.x — Pre-configure structlog at module import time so loggers
# obtained BEFORE :func:`configure_logging` runs still route through
# stdlib logging (not the default :class:`PrintLogger`). This is
# load-bearing: most modules acquire their bound logger at module
# top (``log = get_logger(...)`` at module scope), but the
# application's first ``configure_logging`` call happens later
# inside ``startup()`` / ``app.main:lifespan``. Without this
# pre-config, those top-level loggers cache a PrintLogger that
# writes directly to stderr — bypassing the WatchedFileHandler
# the shared-log fix relies on and the LogCaptureHandler the
# /system/logs endpoint reads from.
#
# We only set ``logger_factory`` here; ``wrapper_class`` and the
# processor chain are deliberately left to ``configure_logging``
# so the level filter + JSON-vs-console renderer can be set from
# the live ``Settings`` object.
structlog.configure(
    logger_factory=structlog.stdlib.LoggerFactory(),
    # IMPORTANT: cache_logger_on_first_use=False so loggers acquired
    # at module import time (before configure_logging tightens the
    # wrapper_class to a level-filtered version) don't get stuck with
    # the default unstructured wrapper. The per-call wrapper rebuild
    # is cheap at the call sites we have; correctness wins.
    cache_logger_on_first_use=False,
)


def _drop_color_message_key(_: object, __: str, event_dict: EventDict) -> EventDict:
    """Strip uvicorn's `color_message` artefact."""
    event_dict.pop("color_message", None)
    return event_dict


def configure_logging(settings: Settings) -> None:
    """Configure stdlib logging + structlog with a shared pipeline."""

    level = getattr(logging, settings.log_level.upper())

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _drop_color_message_key,
    ]

    if settings.log_format == "json":
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        # See module-top configure() — keep the same setting at
        # both call sites so the wrapper_class is consulted fresh
        # on every log call after configure_logging tightens it.
        cache_logger_on_first_use=False,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    # v1.9 — install the ring-buffer capture handler alongside
    # the stderr stream handler. The two are separate so the
    # operator's tail-stderr-style consumption still works
    # exactly as before; the buffer is an additional sink, not
    # a replacement.
    #
    # v1.9.1 fix — order matters here. structlog's
    # ProcessorFormatter mutates record.msg from the original
    # event-dict into a rendered string when it formats. If the
    # stderr handler runs first, the capture handler sees the
    # string version and loses category + context (silently
    # capturing record.event="<the rendered line>" and
    # record.context={}). Operators landed on an "in-memory
    # ring buffer" page that looked completely empty because
    # every record stored had the wrong shape and category
    # filters dropped them. Capture FIRST, then format for
    # stderr.
    #
    # We also DO NOT attach the formatter to the capture handler.
    # The handler reads the raw structlog dict from record.msg
    # directly via LogRecord.emit; running the formatter would
    # again mutate record.msg to a string before the dict-detect
    # branch fires.
    from app.core.log_buffer import LogCaptureHandler

    capture_handler = LogCaptureHandler()

    handlers: list[logging.Handler] = [capture_handler, handler]

    # v1.9.x — shared log file. The API and worker processes each run
    # their own Python VM with their own LogCaptureHandler / ring
    # buffer. Without a shared sink, /system/logs sees only logs from
    # the gunicorn worker that happened to serve the request — worker
    # logs (Plex SSE, playback poller, analyzer, automation
    # scheduler) are invisible. Append-only writes to a single file
    # are atomic on Linux for sub-PIPE_BUF payloads, which all our
    # records are; WatchedFileHandler additionally handles external
    # logrotate-style renames without leaking a stale fd.
    file_handler = _build_shared_file_handler(shared_processors)
    if file_handler is not None:
        handlers.append(file_handler)

    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(level)

    # Quiet noisy libraries unless debug.
    for noisy in ("uvicorn.access", "asyncio", "watchfiles"):
        logging.getLogger(noisy).setLevel(
            logging.DEBUG if settings.debug else logging.WARNING
        )


def _build_shared_file_handler(
    shared_processors: list[Processor],
) -> logging.Handler | None:
    """Return a WatchedFileHandler writing structlog JSON to the
    shared log path, or None if the path isn't writable.

    The handler ALWAYS uses JSON rendering regardless of
    ``settings.log_format`` so the /system/logs endpoint can parse
    rows back into the same LogRecord shape the in-memory buffer
    uses. Operators who want pretty console output on stderr still
    get it via the separate stream handler.

    Path is controlled via ``AUDITARR_LOG_FILE`` env (consumed
    directly here rather than as a Settings field so the addition
    is non-breaking — an operator who doesn't set the var, and
    whose /var/log/auditarr/ isn't writable, just skips file
    logging and gets the legacy in-memory-only behaviour).
    """
    raw_path = os.environ.get("AUDITARR_LOG_FILE") or str(
        _DEFAULT_SHARED_LOG_PATH
    )
    path = Path(raw_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Guard against unbounded growth without depending on
        # external logrotate. Truncate when exceeding the bound;
        # do this BEFORE opening the handler so the inode is
        # preserved (truncate-in-place rather than unlink + recreate).
        if path.exists() and path.stat().st_size > _LOG_FILE_TRUNCATE_BYTES:
            with open(path, "r+b") as fp:
                fp.truncate(0)
        # Touch the file so multiple processes opening concurrently
        # see a stable inode.
        path.touch(exist_ok=True)
    except OSError:
        # Non-writable path (read-only fs, permission mismatch, dir
        # missing on a Docker install). Fall back to in-memory only;
        # the operator gets a degraded log surface but the app keeps
        # running.
        return None

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    file_handler = logging.handlers.WatchedFileHandler(
        str(path), encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    return file_handler


def get_logger(name: str | None = None, **initial_context: Any) -> Any:
    """Return a structlog logger pre-bound with context.

    Implementation note: returns a thin proxy rather than a
    materialised :class:`structlog.stdlib.BoundLogger`. Modules
    typically acquire their logger at import time (``log =
    get_logger(...)`` at module scope), which runs BEFORE
    :func:`configure_logging` installs the JSON renderer and the
    level-filtered wrapper. A materialised BoundLogger captures the
    processor chain at construction time, so a module-top logger
    rendered every event through the default console renderer
    forever — silently bypassing the FileHandler the /system/logs
    endpoint reads.

    The proxy below defers ``structlog.get_logger(name).bind(...)``
    until the first ``.info()`` / ``.warning()`` / ``.debug()`` /
    ``.error()`` / ``.critical()`` / ``.exception()`` call, by
    which time configure_logging has finished and the right
    processor chain is in place.
    """
    return _DeferredLogger(name, initial_context)


class _DeferredLogger:
    """Lazy proxy around ``structlog.get_logger``.

    Materialises a fresh BoundLogger on every log-method call so
    structlog's currently-installed config (renderer chain,
    wrapper_class, level filter) wins — even when the proxy itself
    was constructed before :func:`configure_logging` ran.

    Implements the subset of structlog's BoundLogger surface that
    Auditarr actually uses. Add methods here as new call patterns
    appear in the codebase.
    """

    __slots__ = ("_name", "_context")

    def __init__(self, name: str | None, context: dict[str, Any]) -> None:
        self._name = name
        self._context = context

    def _resolve(self) -> Any:
        bound = structlog.get_logger(self._name)
        if self._context:
            bound = bound.bind(**self._context)
        return bound

    # Bound-logger surface.
    def bind(self, **new_context: Any) -> "_DeferredLogger":
        merged = {**self._context, **new_context}
        return _DeferredLogger(self._name, merged)

    def info(self, event: str, *args: Any, **kw: Any) -> Any:
        return self._resolve().info(event, *args, **kw)

    def warning(self, event: str, *args: Any, **kw: Any) -> Any:
        return self._resolve().warning(event, *args, **kw)

    def debug(self, event: str, *args: Any, **kw: Any) -> Any:
        return self._resolve().debug(event, *args, **kw)

    def error(self, event: str, *args: Any, **kw: Any) -> Any:
        return self._resolve().error(event, *args, **kw)

    def critical(self, event: str, *args: Any, **kw: Any) -> Any:
        return self._resolve().critical(event, *args, **kw)

    def exception(self, event: str, *args: Any, **kw: Any) -> Any:
        return self._resolve().exception(event, *args, **kw)

    # Compatibility with structlog's ``log`` method (some integrations
    # call ``log.log(LOG_LEVEL, "event", ...)``).
    def log(self, level: int, event: str, *args: Any, **kw: Any) -> Any:
        return self._resolve().log(level, event, *args, **kw)
