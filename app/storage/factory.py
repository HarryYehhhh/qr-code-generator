from app.config import settings
from app.storage.base import StorageBackend
from app.storage.local import LocalStorage


def get_storage() -> StorageBackend:
    if settings.ENVIRONMENT == "local":
        return LocalStorage()
    if settings.ENVIRONMENT == "production":
        from app.storage.gcs import GCSStorage

        return GCSStorage()
    raise ValueError(f"Unknown environment: {settings.ENVIRONMENT}")
