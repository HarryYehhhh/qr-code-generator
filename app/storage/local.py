import os

from app.config import settings
from app.storage.base import StorageBackend


class LocalStorage(StorageBackend):
    def __init__(self):
        self.storage_dir = settings.STORAGE_PATH
        os.makedirs(self.storage_dir, exist_ok=True)

    def _full_path(self, path: str) -> str:
        return os.path.join(self.storage_dir, path)

    def save(self, path: str, data: bytes) -> None:
        full = self._full_path(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)

    def get(self, path: str) -> bytes:
        with open(self._full_path(path), "rb") as f:
            return f.read()

    def delete(self, path: str) -> None:
        full = self._full_path(path)
        if os.path.exists(full):
            os.remove(full)

    def exists(self, path: str) -> bool:
        return os.path.exists(self._full_path(path))
