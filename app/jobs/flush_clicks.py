from datetime import datetime, timedelta, timezone

import structlog
from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = structlog.get_logger(__name__)

CLICKS_KEY_PREFIX = "qr:clicks"
FLUSHING_SUFFIX = ":flushing"


def _key_for(bucket: datetime) -> str:
    return f"{CLICKS_KEY_PREFIX}:{bucket.strftime('%Y-%m-%d-%H')}"


def _previous_hour(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)


def flush_previous_hour(redis: Redis, db: Session, batch_size: int = 500) -> int:
    """Drain last hour's click counters from Redis into qr_click_stats.

    Idempotent: re-runs are safe because of ON CONFLICT DO UPDATE plus the
    rename-then-delete pattern that lets a crashed run resume on next call.
    """
    bucket = _previous_hour()
    src_key = _key_for(bucket)
    flushing_key = src_key + FLUSHING_SUFFIX

    # resume case: a previous run renamed but didn't finish
    if not redis.exists(flushing_key):
        if not redis.exists(src_key):
            logger.info("nothing to flush for %s", src_key)
            return 0
        # atomic swap; current-hour clicks land on a different key
        redis.rename(src_key, flushing_key)

    total = _drain(redis, db, flushing_key, bucket, batch_size)
    db.commit()
    redis.delete(flushing_key)
    logger.info("flushed %d rows for bucket %s", total, bucket.isoformat())
    return total


def _drain(redis: Redis, db: Session, key: str, bucket: datetime, batch_size: int) -> int:
    cursor = 0
    total = 0
    while True:
        cursor, fields = redis.hscan(key, cursor=cursor, count=batch_size)
        if fields:
            rows = [
                {
                    "token": _as_str(token),
                    "bucket": bucket,
                    "count": int(count),
                }
                for token, count in fields.items()
            ]
            db.execute(
                text(
                    """
                    INSERT INTO qr_click_stats (qr_token, hour_bucket, click_count)
                    VALUES (:token, :bucket, :count)
                    ON CONFLICT (qr_token, hour_bucket) DO UPDATE
                        SET click_count = qr_click_stats.click_count + EXCLUDED.click_count
                    """
                ),
                rows,
            )
            # keep qr_codes.click_count and last_clicked_at eventually consistent
            db.execute(
                text(
                    """
                    UPDATE qr_codes
                    SET
                        click_count = click_count + :count,
                        last_clicked_at = CASE
                            WHEN last_clicked_at IS NULL OR last_clicked_at < :bucket
                            THEN :bucket
                            ELSE last_clicked_at
                        END
                    WHERE qr_token = :token AND status = 'active'
                    """
                ),
                rows,
            )
            total += len(rows)
        if cursor == 0:
            break
    return total


def _as_str(v) -> str:
    return v.decode() if isinstance(v, bytes) else v
