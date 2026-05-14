"""Custom Prometheus metrics for the QR Code Generator.

All metrics are declared at module level so they are created once and shared
across the process. Import helpers (observe_*) from here rather than
referencing the raw metric objects directly to keep callsites clean.

Metrics:
  qr_redirect_total          Counter, labels: cache_result (hit/miss)
  qr_image_cache_total       Counter, labels: result (hit/miss)
  qr_click_stream_published_total  Counter
  qr_click_stream_consumed_total   Counter
  qr_click_stream_lag        Gauge
  qr_db_pool_in_use          Gauge
"""

from prometheus_client import Counter, Gauge

REDIRECT_TOTAL = Counter(
    "qr_redirect_total",
    "Total redirect requests, split by Redis URL-cache result",
    ["cache_result"],
)

IMAGE_CACHE_TOTAL = Counter(
    "qr_image_cache_total",
    "QR image cache lookups, split by result",
    ["result"],
)

CLICK_STREAM_PUBLISHED = Counter(
    "qr_click_stream_published_total",
    "Click events successfully published to Redis Stream",
)

CLICK_STREAM_CONSUMED = Counter(
    "qr_click_stream_consumed_total",
    "Click events consumed and acked by the worker",
)

CLICK_STREAM_LAG = Gauge(
    "qr_click_stream_lag",
    "Approximate unprocessed entries in the click stream (XLEN minus last delivered)",
)

DB_POOL_IN_USE = Gauge(
    "qr_db_pool_in_use",
    "Number of SQLAlchemy connection pool connections currently checked out",
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def observe_redirect(cache_result: str) -> None:
    """Increment redirect counter for the given cache_result label."""
    REDIRECT_TOTAL.labels(cache_result=cache_result).inc()


def observe_image_cache(result: str) -> None:
    """Increment image cache counter for the given result label."""
    IMAGE_CACHE_TOTAL.labels(result=result).inc()


def observe_publish() -> None:
    """Increment published click counter."""
    CLICK_STREAM_PUBLISHED.inc()


def observe_consume(n: int) -> None:
    """Increment consumed click counter by n."""
    CLICK_STREAM_CONSUMED.inc(n)


def set_stream_lag(v: int) -> None:
    """Set click stream lag gauge."""
    CLICK_STREAM_LAG.set(v)


def set_db_pool_in_use(v: int) -> None:
    """Set DB pool in-use gauge."""
    DB_POOL_IN_USE.set(v)
