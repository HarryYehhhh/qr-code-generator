"""Structured logging configuration using structlog.

Call configure_logging() once at application startup (lifespan / worker main).
All stdlib logging (uvicorn, sqlalchemy, redis) is redirected through the same
processor chain so every log line is a single JSON object.

Usage::

    from app.logging import configure_logging, get_logger
    configure_logging()
    logger = get_logger(__name__)
    logger.info("server started", port=8080)
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog
from opentelemetry import trace


def _add_trace_context(
    logger: Any,  # noqa: ANN401
    method_name: str,
    event_dict: dict,
) -> dict:
    """Inject OTel trace_id / span_id into the event dict.

    When no active span exists (INVALID_SPAN), empty strings are used so
    the fields are always present in every log line.
    """
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    else:
        event_dict["trace_id"] = ""
        event_dict["span_id"] = ""
    return event_dict


def configure_logging() -> None:
    """Set up structlog with JSON output and trace context injection.

    Idempotent: calling twice is safe.
    """
    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    # Shared processors for both structlog-native loggers and stdlib bridge
    shared_processors: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _add_trace_context,
    ]

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    # Avoid duplicate handlers on re-configure
    if not any(isinstance(h, logging.StreamHandler) and h.formatter is formatter
               for h in root_logger.handlers):
        root_logger.handlers.clear()
        root_logger.addHandler(handler)

    root_logger.setLevel(log_level)

    # Quieten noisy third-party loggers
    for name in ("uvicorn.access", "uvicorn.error", "sqlalchemy.engine"):
        logging.getLogger(name).setLevel(log_level)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog BoundLogger (thin wrapper for testability)."""
    return structlog.get_logger(name)
