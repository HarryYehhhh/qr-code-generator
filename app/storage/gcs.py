from google.cloud import storage as gcs

from app.config import settings
from app.storage.base import StorageBackend


class GCSStorage(StorageBackend):
    def __init__(self):
        self.client = gcs.Client()
        self.bucket = self.client.bucket(settings.GCS_BUCKET)

    def save(self, path: str, data: bytes) -> None:
        blob = self.bucket.blob(path)
        blob.upload_from_string(data, content_type="image/png")

    def get(self, path: str) -> bytes:
        blob = self.bucket.blob(path)
        return blob.download_as_bytes()

    def delete(self, path: str) -> None:
        blob = self.bucket.blob(path)
        if blob.exists():
            blob.delete()

    def exists(self, path: str) -> bool:
        blob = self.bucket.blob(path)
        return blob.exists()
