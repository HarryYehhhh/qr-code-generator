"""OpenTelemetry tracing initialisation.

Dual-exporter pattern per ADR-0002:
  - local / local-compose  -> OTLPSpanExporter (OTLP/HTTP -> Jaeger)
  - production             -> CloudTraceSpanExporter (GCP Cloud Trace)
  - other (test / ci)      -> no-op (no exporter, no network calls)

Usage::

    from app.observability import init_tracing, get_tracer
    init_tracing("qr-api")
    tracer = get_tracer(__name__)
"""

from __future__ import annotations

import os
from typing import Literal

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

# Module-level state
_initialized = False
_exporter_kind: Literal["otlp", "gcp", "noop"] = "noop"


def init_tracing(service_name: str) -> None:
    """Initialise the OTel TracerProvider (idempotent).

    Sets the global TracerProvider with the appropriate exporter
    based on settings.ENVIRONMENT.
    """
    global _initialized, _exporter_kind
    if _initialized:
        return

    from app.config import settings  # lazy import avoids circular deps

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": os.getenv("APP_VERSION", "dev"),
        }
    )

    env = settings.ENVIRONMENT

    if env in ("local", "local-compose"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
        exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
        _exporter_kind = "otlp"

    elif env == "production":
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        exporter = CloudTraceSpanExporter()
        _exporter_kind = "gcp"

    else:
        # No exporter: tests / CI — TracerProvider still works, just no export
        _exporter_kind = "noop"
        trace.set_tracer_provider(TracerProvider(resource=resource))
        _apply_auto_instrumentation()
        _initialized = True
        return

    # For local / production: attach the exporter to an SDK provider.
    # If the current provider is already an SDK TracerProvider (e.g. set by
    # tests or a previous call), add a processor directly rather than replacing.
    current = trace.get_tracer_provider()
    if hasattr(current, "add_span_processor"):
        current.add_span_processor(BatchSpanProcessor(exporter))
    else:
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

    # Auto-instrumentation (safe to call multiple times; each instrumentor
    # has its own idempotency guard)
    _apply_auto_instrumentation()

    _initialized = True


def _apply_auto_instrumentation() -> None:
    """Apply auto-instrumentation for FastAPI, SQLAlchemy, Redis, requests."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor().instrument()
    except Exception:
        pass

    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        SQLAlchemyInstrumentor().instrument()
    except Exception:
        pass

    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        RedisInstrumentor().instrument()
    except Exception:
        pass

    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        RequestsInstrumentor().instrument()
    except Exception:
        pass


def get_tracer(name: str) -> trace.Tracer:
    """Return a tracer for the given name (thin wrapper for testability)."""
    return trace.get_tracer(name)


def is_initialized() -> bool:
    """Return True if init_tracing() has already been called."""
    return _initialized


def current_exporter_kind() -> Literal["otlp", "gcp", "noop"]:
    """Return which exporter is active ('otlp', 'gcp', or 'noop')."""
    return _exporter_kind


def reset_for_testing() -> None:
    """Reset module-level state; call from test fixtures only."""
    global _initialized, _exporter_kind
    _initialized = False
    _exporter_kind = "noop"
    # Reset OTel global provider to a fresh no-op one
    trace.set_tracer_provider(TracerProvider())
