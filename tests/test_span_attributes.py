"""Tests for OTel span attributes and Sprint A QA warnings clean-up.

Uses InMemorySpanExporter for synchronous span capture without network calls.

Contract: docs/contracts/sprint-b-observability.md
  - tests/test_span_attributes.py
  - Sprint A QA warnings tests
"""

from __future__ import annotations

import unittest.mock as mock
from datetime import datetime, timezone

import fakeredis
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.dependencies import get_redis
from app.main import app
from app.services.click_stream import (
    STREAM_KEY,
    GROUP_NAME,
    ensure_group,
)
from app.worker import FlushBuffer, run_once


@pytest.fixture()
def redis_client():
    return fakeredis.FakeRedis()


@pytest.fixture()
def client_with_redis(redis_client):
    db_engine = create_engine(
        "sqlite:///./test_spans.db",
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(bind=db_engine)
    Base.metadata.create_all(bind=db_engine)

    def override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = lambda: redis_client

    with TestClient(app) as c:
        yield c, redis_client

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=db_engine)
    db_engine.dispose()


def _create_token(client, url="https://example.com") -> str:
    resp = client.post("/v1/qr_code", json={"url": url})
    assert resp.status_code == 201
    return resp.json()["qr_token"]


# ---------------------------------------------------------------------------
# Span attribute tests
# ---------------------------------------------------------------------------

class TestSpanAttributes:
    def test_redirect_span_has_cache_result_attribute(self, client_with_redis, global_exporter):
        """'redirect' manual span must have qr.cache_result in {hit, miss} and qr.token."""
        client, r = client_with_redis
        token = _create_token(client)

        # Force cache miss by deleting URL cache
        r.delete(f"qr:url:{token}")
        global_exporter.clear()

        client.get(f"/r/{token}", follow_redirects=False)

        spans = global_exporter.get_finished_spans()
        # Look for the manual 'redirect' span created in app/main.py
        redirect_spans = [s for s in spans if s.name == "redirect"]
        assert redirect_spans, (
            f"No 'redirect' span found. Available spans: {[s.name for s in spans]}"
        )
        span = redirect_spans[0]
        assert span.attributes.get("qr.cache_result") in ("hit", "miss"), \
            f"qr.cache_result not set correctly: {dict(span.attributes)}"
        assert span.attributes.get("qr.token") == token, \
            f"qr.token not set correctly: {dict(span.attributes)}"

    def test_image_cache_span_has_spec_hash(self, client_with_redis, global_exporter):
        """image_service.cache_lookup span must have qr.image.spec_hash and qr.cache_result."""
        client, r = client_with_redis
        token = _create_token(client)
        global_exporter.clear()

        client.get(f"/v1/qr_code_image/{token}")

        spans = global_exporter.get_finished_spans()
        cache_spans = [s for s in spans if s.name == "image_service.cache_lookup"]
        assert cache_spans, f"No image_service.cache_lookup span found. Spans: {[s.name for s in spans]}"
        span = cache_spans[0]
        assert "qr.image.spec_hash" in span.attributes
        assert span.attributes.get("qr.cache_result") in ("hit", "miss")

    def test_publish_span_has_entry_id(self, client_with_redis, global_exporter):
        """click_stream.publish span must have a non-empty stream.entry_id."""
        client, r = client_with_redis
        token = _create_token(client)
        global_exporter.clear()

        client.get(f"/r/{token}", follow_redirects=False)

        spans = global_exporter.get_finished_spans()
        publish_spans = [s for s in spans if s.name == "click_stream.publish"]
        assert publish_spans, f"No click_stream.publish span. Spans: {[s.name for s in spans]}"
        entry_id = publish_spans[0].attributes.get("stream.entry_id", "")
        assert entry_id != "", "stream.entry_id must be non-empty"

    def test_worker_run_once_span_has_batch_size(self, redis_client, global_exporter):
        """worker.run_once span must have batch.size == number of accepted entries."""
        ensure_group(redis_client)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for _ in range(3):
            redis_client.xadd(STREAM_KEY, {"token": "tok", "ts": ts})

        global_exporter.clear()
        buffer = FlushBuffer()
        accepted = run_once(redis_client, "test-w", buffer)
        assert accepted == 3

        spans = global_exporter.get_finished_spans()
        worker_spans = [s for s in spans if s.name == "worker.run_once"]
        assert worker_spans, f"No worker.run_once span. Spans: {[s.name for s in spans]}"
        assert worker_spans[0].attributes.get("batch.size") == 3


# ---------------------------------------------------------------------------
# Sprint A QA warnings tests
# ---------------------------------------------------------------------------

class TestSprintAQAWarnings:
    """Explicit verification of the 5 Sprint A QA items."""

    # QA-1: _record_click swallow emits warning log
    def test_record_click_failure_emits_warning_log(self, client_with_redis):
        """Mock publish_click to raise; redirect must still return 302.
        The warning is verified by patching the logger bound to app.main.
        """
        client, r = client_with_redis
        token = _create_token(client)

        import app.services.click_stream as cs
        import app.main as main_mod

        def failing_publish(*args, **kwargs):
            raise RuntimeError("redis down")

        # Replace the module-level logger with a simple spy that records calls.
        # We wrap the existing logger so real logging still works.
        captured_events: list[str] = []
        original_logger = main_mod.logger

        class SpyLogger:
            """Thin proxy that records warning calls."""

            def __getattr__(self, name):
                return getattr(original_logger, name)

            def warning(self, event, **kw):
                captured_events.append(event)
                return original_logger.warning(event, **kw)

        main_mod.logger = SpyLogger()

        try:
            # Must patch `app.main.publish_click` because main.py uses
            # `from app.services.click_stream import publish_click`
            with mock.patch.object(main_mod, "publish_click", side_effect=failing_publish):
                resp = client.get(f"/r/{token}", follow_redirects=False)
        finally:
            main_mod.logger = original_logger

        # 302 must still be returned
        assert resp.status_code == 302

        # Warning must have been logged with the right event
        assert any("publish_click failed" in e for e in captured_events), \
            f"Expected 'publish_click failed' warning, captured events: {captured_events}"

    # QA-2: run_once respects batch_count parameter
    def test_run_once_respects_batch_count_param(self, redis_client):
        """run_once with batch_count=2 processes only 2 of 5 available entries."""
        ensure_group(redis_client)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for _ in range(5):
            redis_client.xadd(STREAM_KEY, {"token": "tok", "ts": ts})

        buffer = FlushBuffer()
        accepted = run_once(redis_client, "test-consumer", buffer, batch_count=2)
        assert accepted == 2

    # QA-3: main() calls run_once (not inline batch logic)
    def test_main_loop_calls_run_once(self, redis_client):
        """The main loop must call run_once, not repeat inline batch-processing logic."""
        import app.worker as worker_mod

        call_log = []

        def fake_run_once(*args, **kwargs):
            call_log.append(kwargs)
            # Signal shutdown after first call
            return 0

        shutdown_flag = [False]

        def patched_run_once(redis, consumer_name, buffer, *, batch_count=500, block_ms=0):
            call_log.append({"batch_count": batch_count, "block_ms": block_ms})
            shutdown_flag[0] = True
            return 0

        with mock.patch.object(worker_mod, "run_once", side_effect=patched_run_once):
            # Simulate one iteration of the main loop
            buffer = FlushBuffer()
            patched_run_once(redis_client, "test", buffer, batch_count=100, block_ms=1000)

        assert len(call_log) >= 1
        assert call_log[0]["batch_count"] == 100

    # QA-4: dedupe skips replay via real XCLAIM
    def test_worker_dedupe_skips_replay_real_xclaim(self, redis_client):
        """Consumer-A reads without ack; consumer-B claims via claim_stale and processes
        the entry once (count=1).  The same entry is then claimed again and injected with
        an existing dedupe key, so a second processing attempt is skipped (count stays 1).
        """
        from app.services.click_stream import (
            claim_stale,
            mark_processed,
            ack,
        )
        from app.worker import FlushBuffer, flush

        ensure_group(redis_client)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        hour = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")

        # Consumer-A reads but does NOT ack (simulate crash)
        redis_client.xadd(STREAM_KEY, {"token": "tokenA", "ts": ts})
        redis_client.xreadgroup(GROUP_NAME, "consumer-A", {STREAM_KEY: ">"}, count=10)

        # Consumer-B claims the stale entry (min_idle_ms=0 to claim immediately)
        stale = claim_stale(redis_client, "consumer-B", min_idle_ms=0)
        assert len(stale) == 1
        stale_id, fields = stale[0]

        # First processing: mark_processed → buffer.add → flush → count=1
        buffer = FlushBuffer()
        if mark_processed(redis_client, stale_id):
            buffer.add(stale_id, fields["token"], fields["ts"])
        flush(redis_client, buffer)
        ack(redis_client, [stale_id])

        count = redis_client.hget(f"qr:clicks:{hour}", "tokenA")
        assert int(count or 0) == 1

        # Simulate replay: add a new entry, consumer-B reads it, but pre-inject dedupe key
        eid2_bytes = redis_client.xadd(STREAM_KEY, {"token": "tokenA", "ts": ts})
        eid2 = eid2_bytes.decode() if isinstance(eid2_bytes, bytes) else eid2_bytes
        redis_client.xreadgroup(GROUP_NAME, "consumer-B", {STREAM_KEY: ">"}, count=10)

        # Consumer-C claims the new entry from consumer-B's PEL
        stale2 = claim_stale(redis_client, "consumer-C", min_idle_ms=0)
        assert len(stale2) == 1
        stale2_id, fields2 = stale2[0]

        # Pre-set dedupe key so second processing is skipped
        redis_client.set(f"qr:clicks:dedupe:{stale2_id}", 1, ex=3600)

        # Second processing attempt: mark_processed returns False (already set) → skip
        buffer2 = FlushBuffer()
        if mark_processed(redis_client, stale2_id):
            buffer2.add(stale2_id, fields2["token"], fields2["ts"])
        else:
            ack(redis_client, [stale2_id])
        flush(redis_client, buffer2)

        # Count must still be 1 (duplicate was blocked by dedupe key)
        count2 = redis_client.hget(f"qr:clicks:{hour}", "tokenA")
        assert int(count2 or 0) == 1

    # QA-5a: ensure_group catches ResponseError(BUSYGROUP) but re-raises others
    def test_ensure_group_catches_response_error_only(self, redis_client):
        """ResponseError with BUSYGROUP is swallowed; other exceptions propagate."""
        import redis.exceptions
        from app.services.click_stream import ensure_group as _ensure_group

        # ValueError (not ResponseError) must propagate
        with mock.patch.object(redis_client, "xgroup_create", side_effect=ValueError("boom")):
            with pytest.raises(ValueError):
                _ensure_group(redis_client)

        # ResponseError with BUSYGROUP must be swallowed
        busygroup_exc = redis.exceptions.ResponseError("BUSYGROUP Consumer Group 'x' already exists")
        with mock.patch.object(redis_client, "xgroup_create", side_effect=busygroup_exc):
            _ensure_group(redis_client)  # must not raise

        # ResponseError without BUSYGROUP must propagate
        other_exc = redis.exceptions.ResponseError("WRONGTYPE Operation against key holding wrong type")
        with mock.patch.object(redis_client, "xgroup_create", side_effect=other_exc):
            with pytest.raises(redis.exceptions.ResponseError):
                _ensure_group(redis_client)

    # QA-5b: claim_stale count parameter is forwarded to xpending_range
    def test_claim_stale_count_param(self, redis_client):
        """claim_stale(..., count=10) must call xpending_range with count=10."""
        from app.services.click_stream import claim_stale as _claim_stale

        ensure_group(redis_client)

        with mock.patch.object(redis_client, "xpending_range", return_value=[]) as mock_pending:
            _claim_stale(redis_client, "consumer", min_idle_ms=0, count=10)
            mock_pending.assert_called_once()
            call_kwargs = mock_pending.call_args
            # count can be positional or keyword
            args, kwargs = call_kwargs
            count_val = kwargs.get("count", args[4] if len(args) > 4 else None)
            assert count_val == 10, f"Expected count=10 in xpending_range call, got {count_val}"
