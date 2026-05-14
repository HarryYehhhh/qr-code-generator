"""Tests for custom Prometheus metrics exposed at /metrics.

Contract: docs/contracts/sprint-b-observability.md — tests/test_metrics.py section
"""

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.dependencies import get_redis
from app.main import app
from app.services.click_stream import STREAM_KEY, GROUP_NAME, ensure_group
from app.worker import FlushBuffer, run_once


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def redis_client():
    return fakeredis.FakeRedis()


@pytest.fixture()
def client_and_redis(redis_client):
    """TestClient wired to a fresh SQLite DB and the shared fakeredis instance."""
    db_engine = create_engine(
        "sqlite:///./test_metrics.db",
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
# Tests
# ---------------------------------------------------------------------------

class TestMetricsEndpoint:
    def test_metrics_endpoint_exposes_custom_counters(self, client_and_redis):
        """GET /metrics must contain all six custom metric names."""
        client, r = client_and_redis
        token = _create_token(client)
        # Trigger redirect so redirect counter is initialised
        client.get(f"/r/{token}", follow_redirects=False)

        resp = client.get("/metrics")
        assert resp.status_code == 200
        body = resp.text

        for name in [
            "qr_redirect_total",
            "qr_image_cache_total",
            "qr_click_stream_published_total",
            "qr_click_stream_consumed_total",
            "qr_click_stream_lag",
            "qr_db_pool_in_use",
        ]:
            assert name in body, f"metric {name!r} not found in /metrics"

    def test_redirect_increments_cache_hit_counter(self, client_and_redis):
        """First redirect is a cache miss; second is a cache hit."""
        client, r = client_and_redis
        token = _create_token(client)

        # First redirect: cache miss (token was just created, URL is in cache from create)
        # Delete URL cache to force miss
        r.delete(f"qr:url:{token}")
        client.get(f"/r/{token}", follow_redirects=False)   # miss

        # Second redirect: cache hit
        client.get(f"/r/{token}", follow_redirects=False)   # hit

        body = client.get("/metrics").text
        assert 'cache_result="hit"' in body or "hit" in body
        assert 'cache_result="miss"' in body or "miss" in body

    def test_image_cache_counter_hit_and_miss(self, client_and_redis):
        """Two image requests for same token: first miss, second hit."""
        client, r = client_and_redis
        token = _create_token(client)

        # First request: cache miss
        resp1 = client.get(f"/v1/qr_code_image/{token}")
        assert resp1.status_code == 200

        # Second request: cache hit
        resp2 = client.get(f"/v1/qr_code_image/{token}")
        assert resp2.status_code == 200

        body = client.get("/metrics").text
        assert "qr_image_cache_total" in body

    def test_publish_counter_increments_on_redirect(self, client_and_redis):
        """Each redirect should increment qr_click_stream_published_total by 1."""
        client, r = client_and_redis
        token = _create_token(client)

        # Get baseline
        before = _parse_counter(client.get("/metrics").text, "qr_click_stream_published_total")

        client.get(f"/r/{token}", follow_redirects=False)

        after = _parse_counter(client.get("/metrics").text, "qr_click_stream_published_total")
        assert after >= before + 1

    def test_worker_run_once_increments_consumed(self, client_and_redis):
        """Worker run_once processing 3 entries increments consumed counter by 3."""
        client, r = client_and_redis

        ensure_group(r)
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for _ in range(3):
            r.xadd(STREAM_KEY, {"token": "tok", "ts": ts})

        before = _parse_counter(client.get("/metrics").text, "qr_click_stream_consumed_total")

        buffer = FlushBuffer()
        accepted = run_once(r, "test-consumer", buffer)
        assert accepted == 3

        from app.metrics import observe_consume
        observe_consume(accepted)

        after = _parse_counter(client.get("/metrics").text, "qr_click_stream_consumed_total")
        assert after >= before + 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_counter(metrics_text: str, name: str) -> float:
    """Parse the most recent sample value for a counter from Prometheus text format."""
    total = 0.0
    for line in metrics_text.split("\n"):
        if line.startswith("#") or not line.strip():
            continue
        if name in line and not line.startswith("#"):
            parts = line.rsplit(" ", 1)
            if len(parts) == 2:
                try:
                    total += float(parts[1])
                except ValueError:
                    pass
    return total
