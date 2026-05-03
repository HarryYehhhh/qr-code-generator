from functools import lru_cache

from redis import Redis

from app.config import settings


@lru_cache(maxsize=1)
def _redis_client() -> Redis:
    return Redis.from_url(settings.REDIS_URL)


def get_redis() -> Redis:
    return _redis_client()
