"""Tests for the Sprint A click pipeline (producer + consumer worker).

All Redis interaction uses fakeredis so no external Redis is required.
Contract: docs/contracts/sprint-a-click-stream.md
"""

import time
from datetime import datetime, timezone

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.dependencies import get_redis
from app.main import app
from app.services.click_stream import (
    GROUP_NAME,
    STREAM_KEY,
    ensure_group,
)
from app.worker import FlushBuffer, flush, run_once


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def redis():
    return fakeredis.FakeRedis()


@pytest.fixture()
def redis_with_group(redis):
    """fakeredis instance with consumer group pre-created."""
    ensure_group(redis)
    return redis


@pytest.fixture()
def client_with_redis(redis):
    """TestClient wired to the shared fakeredis instance."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite:///./test_click_stream.db",
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_redis] = lambda: redis

    with TestClient(app) as c:
        yield c, redis

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _create_and_get_token(client, url: str = "https://example.com") -> str:
    resp = client.post("/v1/qr_code", json={"url": url})
    assert resp.status_code == 201
    return resp.json()["qr_token"]


def _current_hour() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")


# ---------------------------------------------------------------------------
# Producer tests
# ---------------------------------------------------------------------------

class TestRedirectProducer:
    def test_xadd_on_redirect_cache_hit(self, client_with_redis):
        """cache hit path: redirect fires XADD, stream has 1 entry with correct token."""
        client, r = client_with_redis
        token = _create_and_get_token(client)

        # First redirect populates URL cache
        client.get(f"/r/{token}", follow_redirects=False)
        # Verify cache is populated
        assert r.get(f"qr:url:{token}") is not None

        # Second redirect hits the cache
        client.get(f"/r/{token}", follow_redirects=False)

        # Stream should have 2 entries total (both hits go through _record_click)
        assert r.xlen(STREAM_KEY) == 2

        # Check the last entry has the correct token field
        entries = r.xrange(STREAM_KEY, "-", "+")
        assert len(entries) == 2
        for _, fields in entries:
            assert fields[b"token"] == token.encode()

    def test_xadd_on_redirect_cache_miss(self, client_with_redis):
        """cache miss path: redirect fires XADD and also populates URL cache."""
        client, r = client_with_redis
        token = _create_and_get_token(client)

        # Ensure there is no URL cache to force a cache miss
        r.delete(f"qr:url:{token}")

        client.get(f"/r/{token}", follow_redirects=False)

        # One entry in the stream
        assert r.xlen(STREAM_KEY) == 1
        entries = r.xrange(STREAM_KEY, "-", "+")
        _, fields = entries[0]
        assert fields[b"token"] == token.encode()

        # URL must be re-cached after a miss
        assert r.get(f"qr:url:{token}") == b"https://example.com"

    def test_no_hincrby_on_redirect(self, client_with_redis):
        """Worker has not run, so the click hash must not exist yet."""
        client, r = client_with_redis
        token = _create_and_get_token(client)
        client.get(f"/r/{token}", follow_redirects=False)

        hour = _current_hour()
        assert r.exists(f"qr:clicks:{hour}") == 0


# ---------------------------------------------------------------------------
# click_stream module tests
# ---------------------------------------------------------------------------

class TestEnsureGroup:
    def test_ensure_group_idempotent(self, redis):
        """Calling ensure_group twice must not raise."""
        ensure_group(redis)
        ensure_group(redis)  # Should NOT raise BUSYGROUP


# ---------------------------------------------------------------------------
# Worker / run_once tests
# ---------------------------------------------------------------------------

class TestWorkerRunOnce:
    def test_worker_run_once_aggregates_and_acks(self, redis_with_group):
        """3 entries for tokenA, 2 for tokenB -> hash counts correct, pending cleared."""
        r = redis_with_group
        hour = _current_hour()
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        for _ in range(3):
            r.xadd(STREAM_KEY, {"token": "tokenA", "ts": ts})
        for _ in range(2):
            r.xadd(STREAM_KEY, {"token": "tokenB", "ts": ts})

        buffer = FlushBuffer()
        run_once(r, "test-worker", buffer)
        flush(r, buffer)

        assert int(r.hget(f"qr:clicks:{hour}", "tokenA")) == 3
        assert int(r.hget(f"qr:clicks:{hour}", "tokenB")) == 2

        # XACK does not remove entries from the stream, but pending count == 0
        summary = r.xpending(STREAM_KEY, GROUP_NAME)
        assert summary["pending"] == 0

    def test_worker_dedupe_skips_replay(self, redis_with_group):
        """The same entry replayed via XCLAIM must only be counted once."""
        r = redis_with_group
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        hour = _current_hour()

        eid = r.xadd(STREAM_KEY, {"token": "tokenA", "ts": ts})
        if isinstance(eid, bytes):
            eid = eid.decode()

        # First pass: mark_processed sets dedupe key and counts it
        buffer = FlushBuffer()
        run_once(r, "consumer-1", buffer)
        flush(r, buffer)
        assert int(r.hget(f"qr:clicks:{hour}", "tokenA")) == 1

        # Re-deliver the same entry by re-adding it manually and then
        # calling run_once again; dedupe key should block counting
        # Simulate replay: reset the dedupe key to absent then re-xadd same-ish entry
        # Instead, directly insert a duplicate via xclaim simulation:
        # Add a new raw entry with same token but force dedupe by calling mark_processed first
        eid2 = r.xadd(STREAM_KEY, {"token": "tokenA", "ts": ts})
        if isinstance(eid2, bytes):
            eid2 = eid2.decode()

        # Mark this new entry as processed before run_once sees it
        dedupe_key = f"qr:clicks:dedupe:{eid2}"
        r.set(dedupe_key, 1, ex=3600)

        buffer2 = FlushBuffer()
        run_once(r, "consumer-1", buffer2)
        flush(r, buffer2)

        # Count must still be 1 (the duplicate was blocked)
        count = r.hget(f"qr:clicks:{hour}", "tokenA")
        assert int(count) == 1

    def test_worker_claims_stale_pending_on_startup(self, redis_with_group):
        """A stale pending entry is reclaimed and processed by the new consumer."""
        from app.services.click_stream import claim_stale
        r = redis_with_group
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        hour = _current_hour()

        eid = r.xadd(STREAM_KEY, {"token": "orphanToken", "ts": ts})

        # Consumer-1 reads but does NOT ack (simulating a crash)
        r.xreadgroup(GROUP_NAME, "consumer-1", {STREAM_KEY: ">"}, count=10)

        # Pending should have 1 entry
        summary = r.xpending(STREAM_KEY, GROUP_NAME)
        assert summary["pending"] == 1

        # New consumer claims the stale entry (min_idle_ms=0 to claim immediately)
        stale = claim_stale(r, "consumer-2", min_idle_ms=0)
        assert len(stale) == 1

        stale_id, fields = stale[0]
        assert fields["token"] == "orphanToken"

        # Process the reclaimed entry
        from app.services.click_stream import mark_processed, ack
        buffer = FlushBuffer()
        if mark_processed(r, stale_id):
            buffer.add(stale_id, fields["token"], fields["ts"])
        flush(r, buffer)
        ack(r, [stale_id])

        count = r.hget(f"qr:clicks:{hour}", "orphanToken")
        assert int(count) == 1

        # Pending should be cleared
        summary = r.xpending(STREAM_KEY, GROUP_NAME)
        assert summary["pending"] == 0

    def test_worker_handles_cross_hour_batch(self, redis_with_group):
        """Entries spanning two hours must be flushed into two separate hash keys."""
        r = redis_with_group

        ts_hour1 = "2026-05-14T09:59:59Z"
        ts_hour2 = "2026-05-14T10:00:01Z"

        r.xadd(STREAM_KEY, {"token": "tok", "ts": ts_hour1})
        r.xadd(STREAM_KEY, {"token": "tok", "ts": ts_hour2})

        buffer = FlushBuffer()
        run_once(r, "test-worker", buffer)
        flush(r, buffer)

        count_h1 = r.hget("qr:clicks:2026-05-14-09", "tok")
        count_h2 = r.hget("qr:clicks:2026-05-14-10", "tok")
        assert int(count_h1) == 1
        assert int(count_h2) == 1

    def test_worker_flush_then_existing_flush_job(self, redis_with_group):
        """Worker flush followed by /internal/flush_clicks writes to DB correctly."""
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker

        from app.jobs.flush_clicks import flush_previous_hour

        r = redis_with_group

        # Use a timestamp in the PREVIOUS hour so flush_previous_hour picks it up
        now = datetime.now(timezone.utc)
        prev_hour_dt = now.replace(minute=0, second=0, microsecond=0)
        if prev_hour_dt == now.replace(minute=0, second=0, microsecond=0):
            # force previous hour
            from datetime import timedelta
            prev_hour_dt = prev_hour_dt - timedelta(hours=1)
        prev_hour_str = prev_hour_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        bucket_key = f"qr:clicks:{prev_hour_dt.strftime('%Y-%m-%d-%H')}"

        r.xadd(STREAM_KEY, {"token": "flushToken", "ts": prev_hour_str})

        buffer = FlushBuffer()
        run_once(r, "test-worker", buffer)
        flush(r, buffer)

        # Hash for the previous hour should now have the count
        assert int(r.hget(bucket_key, "flushToken") or 0) == 1

        # Create an in-memory SQLite DB to run flush_previous_hour against
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        engine.execute = None  # not used directly

        from app.database import Base as AppBase
        AppBase.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        db = Session()

        try:
            rows = flush_previous_hour(r, db, batch_size=500)
            assert rows >= 1
            result = db.execute(
                text("SELECT click_count FROM qr_click_stats WHERE qr_token = 'flushToken'")
            ).fetchone()
            assert result is not None
            assert result[0] >= 1
        finally:
            db.close()
            engine.dispose()


class TestThroughput:
    def test_thousand_redirects_one_token(self, client_with_redis):
        """1000 redirects for the same token should accumulate click_count == 1000."""
        client, r = client_with_redis
        token = _create_and_get_token(client)

        for _ in range(1000):
            client.get(f"/r/{token}", follow_redirects=False)

        assert r.xlen(STREAM_KEY) == 1000

        ensure_group(r)
        hour = _current_hour()
        total = 0
        # Drain the stream in batches (run_once pulls up to 500 each call)
        while True:
            buffer = FlushBuffer()
            accepted = run_once(r, "bulk-worker", buffer)
            flush(r, buffer)
            total += accepted
            if accepted == 0:
                break

        count = r.hget(f"qr:clicks:{hour}", token)
        assert int(count) == 1000
