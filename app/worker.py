"""Click-stream consumer worker.

Reads click events from `clicks:stream` via XREADGROUP, aggregates counts
in-memory, and periodically flushes to `qr:clicks:{YYYY-MM-DD-HH}` hashes
so the existing hourly flush job can drain them into the DB.

Usage:
    python -m app.worker

Environment variables:
    CONSUMER_NAME          Unique name for this process (default: worker-{host}-{pid})
    BATCH_SIZE             Max entries to pull per XREADGROUP call (default: 500)
    FLUSH_INTERVAL_SECONDS Seconds between forced flushes (default: 5)
    CLAIM_MIN_IDLE_MS      Min idle time (ms) for XCLAIM on startup (default: 60000)
    REDIS_URL              Redis connection URL (default from app.config.settings)
"""

import logging
import os
import signal
import socket
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from redis import Redis

from app.config import settings
from app.services.click_stream import (
    GROUP_NAME,
    STREAM_KEY,
    ack,
    claim_stale,
    ensure_group,
    mark_processed,
    read_batch,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [worker] %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class FlushBuffer:
    """In-memory accumulator for one flush cycle."""

    # {hour_bucket: {token: count}}
    counts: dict[str, dict[str, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int))
    )
    ids: list[str] = field(default_factory=list)

    def add(self, entry_id: str, token: str, ts: str) -> None:
        hour = _ts_to_hour(ts)
        self.counts[hour][token] += 1
        self.ids.append(entry_id)

    def is_empty(self) -> bool:
        return not self.ids

    def clear(self) -> None:
        self.counts.clear()
        self.ids.clear()


def _ts_to_hour(ts: str) -> str:
    """Convert ISO-8601 UTC timestamp to 'YYYY-MM-DD-HH' bucket string."""
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        # Fallback: use current hour so we never lose a click
        dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%d-%H")


def run_once(redis: Redis, consumer_name: str, buffer: FlushBuffer) -> int:
    """Pull one batch, update buffer, return number of new entries accepted.

    Designed for easy unit-testing: callers control the buffer and can inspect
    it after the call without starting the full main loop.
    Does NOT flush — the caller decides when to flush.
    """
    batch = read_batch(redis, consumer_name, count=500, block_ms=0)
    accepted = 0
    for entry_id, fields in batch:
        if not mark_processed(redis, entry_id):
            # Duplicate (replayed via XCLAIM) — ack and skip
            ack(redis, [entry_id])
            continue
        token = fields.get("token", "")
        ts = fields.get("ts", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        buffer.add(entry_id, token, ts)
        accepted += 1
    return accepted


def flush(redis: Redis, buffer: FlushBuffer) -> None:
    """Write accumulated counts to Redis hashes and ack all buffered IDs."""
    if buffer.is_empty():
        return
    for hour, tokens in buffer.counts.items():
        for token, count in tokens.items():
            redis.hincrby(f"qr:clicks:{hour}", token, count)
    ack(redis, buffer.ids)
    logger.info(
        "flushed %d entries across %d hour buckets",
        len(buffer.ids),
        len(buffer.counts),
    )
    buffer.clear()


def main() -> None:
    consumer_name = os.environ.get(
        "CONSUMER_NAME",
        f"worker-{socket.gethostname()}-{os.getpid()}",
    )
    batch_size = int(os.environ.get("BATCH_SIZE", "500"))
    flush_interval = float(os.environ.get("FLUSH_INTERVAL_SECONDS", "5"))
    claim_min_idle_ms = int(os.environ.get("CLAIM_MIN_IDLE_MS", "60000"))

    redis = Redis.from_url(settings.REDIS_URL)
    buffer = FlushBuffer()

    # Graceful shutdown: flush buffer on SIGTERM / SIGINT
    _shutdown = [False]

    def _handle_signal(signum, frame):  # noqa: ANN001
        logger.info("received signal %s, draining buffer…", signum)
        _shutdown[0] = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info("starting consumer %s on stream %s group %s", consumer_name, STREAM_KEY, GROUP_NAME)
    ensure_group(redis)

    # Reclaim entries orphaned by a previously crashed worker
    stale = claim_stale(redis, consumer_name, claim_min_idle_ms)
    if stale:
        logger.info("reclaimed %d stale entries on startup", len(stale))
        for entry_id, fields in stale:
            if mark_processed(redis, entry_id):
                token = fields.get("token", "")
                ts = fields.get("ts", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
                buffer.add(entry_id, token, ts)
            else:
                ack(redis, [entry_id])

    last_flush = time.monotonic()

    while not _shutdown[0]:
        batch = read_batch(redis, consumer_name, count=batch_size, block_ms=1000)
        for entry_id, fields in batch:
            if not mark_processed(redis, entry_id):
                ack(redis, [entry_id])
                continue
            token = fields.get("token", "")
            ts = fields.get("ts", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            buffer.add(entry_id, token, ts)

        now = time.monotonic()
        should_flush = (
            len(buffer.ids) >= batch_size
            or (now - last_flush) >= flush_interval
        )
        if should_flush:
            flush(redis, buffer)
            last_flush = now

    # Drain remaining buffer before exit
    flush(redis, buffer)
    logger.info("worker %s exiting cleanly", consumer_name)
    sys.exit(0)


if __name__ == "__main__":
    main()
