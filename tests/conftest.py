import os

import fakeredis
import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.dependencies import get_redis
from app.main import app

TEST_DATABASE_URL = "sqlite:///./test_qr_codes.db"

engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(bind=engine)

# Global in-memory span exporter shared across all tests.
# Set up once per session so OTel's TracerProvider is configured exactly once.
_GLOBAL_EXPORTER = InMemorySpanExporter()


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True, scope="session")
def set_test_environment():
    """Set ENVIRONMENT=test so OTel uses no-op tracer (no network calls).

    Also initialise a session-scoped SDK TracerProvider with an InMemorySpanExporter
    so span attribute tests can inspect finished spans without network calls.
    """
    original = os.environ.get("ENVIRONMENT")
    os.environ["ENVIRONMENT"] = "test"

    # Initialise a real SDK provider with the global exporter (done once per session).
    sdk_provider = TracerProvider()
    sdk_provider.add_span_processor(SimpleSpanProcessor(_GLOBAL_EXPORTER))
    try:
        trace.set_tracer_provider(sdk_provider)
    except Exception:
        # OTel may warn or block if already set; proceed anyway.
        pass

    yield

    if original is None:
        os.environ.pop("ENVIRONMENT", None)
    else:
        os.environ["ENVIRONMENT"] = original


@pytest.fixture()
def global_exporter() -> InMemorySpanExporter:
    """Return the session-scoped InMemorySpanExporter, cleared before each test."""
    _GLOBAL_EXPORTER.clear()
    return _GLOBAL_EXPORTER


@pytest.fixture(autouse=True)
def setup_db():
    """每個測試前建表，測試後清表。"""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def fake_redis():
    return fakeredis.FakeRedis()


@pytest.fixture()
def client(fake_redis):
    """提供覆蓋了 DB 與 Redis dependency 的 TestClient。"""
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = lambda: fake_redis
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
