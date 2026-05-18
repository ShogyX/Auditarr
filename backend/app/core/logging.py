"""Structured logging configuration."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from app.core.settings import Settings

# Reusable category constants — every log call should set ``category``.
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
        cache_logger_on_first_use=True,
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

    root = logging.getLogger()
    root.handlers = [capture_handler, handler]
    root.setLevel(level)

    # Quiet noisy libraries unless debug.
    for noisy in ("uvicorn.access", "asyncio", "watchfiles"):
        logging.getLogger(noisy).setLevel(
            logging.DEBUG if settings.debug else logging.WARNING
        )


def get_logger(name: str | None = None, **initial_context: Any) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger pre-bound with context."""
    logger = structlog.get_logger(name)
    if initial_context:
        logger = logger.bind(**initial_context)
    return logger  # type: ignore[return-value]
