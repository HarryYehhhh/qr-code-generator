"""Redis Streams helpers for the click event pipeline.

Producer side: publish_click / ensure_group
Consumer side: read_batch / claim_stale / ack / mark_processed
"""

from datetime import datetime, timezone

from redis import Redis

STREAM_KEY = "clicks:stream"
GROUP_NAME = "click-aggregator"
_DEDUPE_TTL = 3600  # seconds


def publish_click(redis: Redis, token: str, ts: str) -> str:
    """XADD one click event; returns the entry ID."""
    entry_id = redis.xadd(
        STREAM_KEY,
        {"token": token, "ts": ts},
        maxlen=100000,
        approximate=True,
    )
    # redis-py returns bytes; decode for convenience
    if isinstance(entry_id, bytes):
        return entry_id.decode()
    return entry_id


def ensure_group(redis: Redis) -> None:
    """Create the consumer group if it does not exist.

    MKSTREAM creates the stream key as well so no prior XADD is required.
    BUSYGROUP means the group already exists — swallow it.
    """
    try:
        redis.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
    except Exception as exc:
        # redis-py raises ResponseError("BUSYGROUP ...")
        if "BUSYGROUP" not in str(exc):
            raise


def read_batch(
    redis: Redis,
    consumer_name: str,
    count: int,
    block_ms: int,
) -> list[tuple[str, dict]]:
    """XREADGROUP from the stream.

    Returns [(entry_id, fields_dict), ...].
    Fields values are decoded from bytes to str.
    """
    result = redis.xreadgroup(
        GROUP_NAME,
        consumer_name,
        {STREAM_KEY: ">"},
        count=count,
        block=block_ms,
    )
    if not result:
        return []
    entries = result[0][1]  # [(id, fields), ...]
    return [(_decode(eid), _decode_fields(fields)) for eid, fields in entries]


def claim_stale(
    redis: Redis,
    consumer_name: str,
    min_idle_ms: int,
) -> list[tuple[str, dict]]:
    """XPENDING + XCLAIM to reclaim entries idle longer than min_idle_ms.

    Returns same format as read_batch.
    """
    pending = redis.xpending_range(
        STREAM_KEY, GROUP_NAME, "-", "+", count=500
    )
    if not pending:
        return []

    stale_ids = [
        p["message_id"]
        for p in pending
        if p.get("time_since_delivered", min_idle_ms + 1) >= min_idle_ms
    ]
    if not stale_ids:
        return []

    claimed = redis.xclaim(
        STREAM_KEY, GROUP_NAME, consumer_name, min_idle_time=min_idle_ms, message_ids=stale_ids
    )
    return [(_decode(eid), _decode_fields(fields)) for eid, fields in claimed]


def ack(redis: Redis, entry_ids: list[str]) -> None:
    """XACK a list of entry IDs."""
    if entry_ids:
        redis.xack(STREAM_KEY, GROUP_NAME, *entry_ids)


def mark_processed(redis: Redis, entry_id: str) -> bool:
    """SET dedupe key NX EX 3600.

    Returns True if this is the first time this entry is processed.
    """
    key = f"qr:clicks:dedupe:{entry_id}"
    result = redis.set(key, 1, ex=_DEDUPE_TTL, nx=True)
    return bool(result)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _decode(value) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return value


def _decode_fields(fields: dict) -> dict:
    return {_decode(k): _decode(v) for k, v in fields.items()}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
