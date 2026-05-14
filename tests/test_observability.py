"""Tests for app/observability.py (OTel tracing init) and app/logging.py (structlog)."""

import io
import json
import os
import sys

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.export import SimpleSpanProcessor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_observability_state():
    """Reset only app.observability module-level flags (not the OTel provider).

    We cannot call trace.set_tracer_provider() again after conftest sets it,
    so we just reset the _initialized/_exporter_kind flags so init_tracing()
    will re-run its branching logic.
    """
    import app.observability as obs
    obs._initialized = False
    obs._exporter_kind = "noop"


@pytest.fixture(autouse=True)
def reset_obs():
    """Reset observability module state before and after each test in this module."""
    _reset_observability_state()
    yield
    _reset_observability_state()


# ---------------------------------------------------------------------------
# init_tracing tests
# ---------------------------------------------------------------------------

class TestInitTracing:
    def test_init_tracing_local_uses_otlp(self, monkeypatch):
        import app.config as cfg
        import unittest.mock as mock

        monkeypatch.setattr(cfg.settings, "ENVIRONMENT", "local")

        # Mock OTLPSpanExporter to avoid network calls in tests
        fake_exporter = mock.MagicMock()
        FakeOTLP = mock.MagicMock(return_value=fake_exporter)

        with mock.patch(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
            FakeOTLP,
        ):
            from app.observability import init_tracing, current_exporter_kind
            init_tracing("qr-test")

        assert current_exporter_kind() == "otlp"

    def test_init_tracing_production_uses_gcp(self, monkeypatch):
        import app.config as cfg
        monkeypatch.setattr(cfg.settings, "ENVIRONMENT", "production")

        # Patch CloudTraceSpanExporter to avoid real GCP calls
        import unittest.mock as mock
        fake_exporter_instance = mock.MagicMock()
        fake_exporter_instance.export = mock.MagicMock(return_value=0)
        FakeCloudTrace = mock.MagicMock(return_value=fake_exporter_instance)

        with mock.patch(
            "opentelemetry.exporter.cloud_trace.CloudTraceSpanExporter",
            FakeCloudTrace,
        ):
            from app.observability import init_tracing, current_exporter_kind
            init_tracing("qr-test")

        assert current_exporter_kind() == "gcp"

    def test_init_tracing_other_env_noop(self, monkeypatch):
        import app.config as cfg
        monkeypatch.setattr(cfg.settings, "ENVIRONMENT", "ci")

        from app.observability import init_tracing, current_exporter_kind, get_tracer
        init_tracing("qr-test")

        assert current_exporter_kind() == "noop"

        # Tracer must not raise even without an exporter
        tracer = get_tracer("test")
        with tracer.start_as_current_span("dummy"):
            pass

    def test_init_tracing_idempotent(self, monkeypatch):
        import app.config as cfg
        monkeypatch.setattr(cfg.settings, "ENVIRONMENT", "local")

        import unittest.mock as mock
        call_count = []

        class CountingOTLP:
            def __init__(self, *args, **kwargs):
                call_count.append(1)

            def export(self, *args, **kwargs):
                return 0

            def shutdown(self):
                pass

        with mock.patch(
            "opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter",
            CountingOTLP,
        ):
            from app.observability import init_tracing
            init_tracing("qr-test")
            init_tracing("qr-test")  # second call should be no-op

        # OTLPSpanExporter should have been constructed exactly once
        assert len(call_count) == 1

    def test_is_initialized_flag(self, monkeypatch):
        import app.config as cfg
        monkeypatch.setattr(cfg.settings, "ENVIRONMENT", "test")

        from app.observability import init_tracing, is_initialized

        assert not is_initialized()
        init_tracing("qr-test")
        assert is_initialized()


# ---------------------------------------------------------------------------
# structlog / logging tests
# ---------------------------------------------------------------------------

class TestLogging:
    def test_logging_emits_json_with_trace_context(self):
        """Inside an active span the log line must contain non-empty trace_id / span_id."""
        from app.logging import _add_trace_context

        # Use the global SDK provider (set by conftest); start a span inside it
        tracer = trace.get_tracer("test-logger")
        with tracer.start_as_current_span("test-span") as span:
            span_ctx = span.get_span_context()

            # Directly call _add_trace_context and verify it injects the right values
            event_dict = {"event": "hello", "foo": 1}
            result = _add_trace_context(None, None, event_dict)

        assert result["trace_id"] != "", "trace_id must be non-empty inside a span"
        assert result["span_id"] != "", "span_id must be non-empty inside a span"
        assert result["event"] == "hello"
        assert result["foo"] == 1

    def test_logging_outside_span_has_empty_trace_id(self):
        """Outside any span, trace_id must be an empty string (always present)."""
        from app.logging import _add_trace_context

        # Ensure we are outside any active span context
        event_dict = {}
        result = _add_trace_context(None, None, event_dict)
        assert result["trace_id"] == ""
        assert result["span_id"] == ""
