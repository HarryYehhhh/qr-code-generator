"""Structured logging configuration using structlog.

Call configure_logging() once at application startup (lifespan).
All stdlib logging (uvicorn, sqlalchemy, redis) is redirected through the same
processor chain so every log line is a single JSON object that GCP Cloud
Logging parses natively.

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


def configure_logging() -> None:
    """Set up structlog with JSON output. Idempotent."""
    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    # Shared processors for both structlog-native loggers and stdlib bridge
    shared_processors: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
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
