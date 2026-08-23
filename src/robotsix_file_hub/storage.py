"""Storage backend abstraction — local filesystem."""

import asyncio
import hashlib
import logging
from abc import ABC, abstractmethod
from pathlib import Path

from .config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()


class StorageError(Exception):
    """Raised when a storage operation fails."""


class StorageBackend(ABC):
    @abstractmethod
    async def save(self, file_id: str, content: bytes) -> str:
        """Save file bytes. Returns the storage path/identifier."""
        ...

    @abstractmethod
    async def get(self, path: str) -> bytes:
        """Retrieve file bytes by storage path."""
        ...

    @abstractmethod
    async def delete(self, path: str) -> None:
        """Delete stored file bytes by storage path."""
        ...


class LocalStorageBackend(StorageBackend):
    """Local filesystem ``StorageBackend``.

    Stores files under *base_path* on disk; each public method offloads the
    I/O call to a thread and wraps ``OSError`` in ``StorageError``.
    """

    def __init__(self, base_path: str) -> None:
        self.base_path = Path(base_path)

    async def save(self, file_id: str, content: bytes) -> str:
        """Write *content* to a file named *file_id*, returning its path.

        Offloads the ``write_bytes`` call to a thread; raises
        ``StorageError`` (wrapping ``OSError``) on failure.
        """
        self.base_path.mkdir(parents=True, exist_ok=True)
        file_path = self.base_path / file_id

        def _write() -> None:
            file_path.write_bytes(content)

        try:
            await asyncio.to_thread(_write)
        except OSError as exc:
            logger.error("Local storage save failed (file_id=%s): %s", file_id, exc)
            raise StorageError(f"Failed to write file: {exc}") from exc

        return str(file_path)

    async def get(self, path: str) -> bytes:
        """Read and return the file bytes at *path*.

        Offloads the ``read_bytes`` call to a thread; raises
        ``StorageError`` (wrapping ``OSError``) on failure.
        """
        file_path = Path(path)

        def _read() -> bytes:
            return file_path.read_bytes()

        try:
            return await asyncio.to_thread(_read)
        except OSError as exc:
            logger.error("Local storage read failed (path=%s): %s", path, exc)
            raise StorageError(f"Failed to read file: {exc}") from exc

    async def delete(self, path: str) -> None:
        """Remove the file at *path* if it exists.

        Offloads the ``unlink`` call to a thread; raises ``StorageError``
        (wrapping ``OSError``) on failure.
        """
        file_path = Path(path)

        def _remove() -> None:
            file_path.unlink(missing_ok=True)

        try:
            await asyncio.to_thread(_remove)
        except OSError as exc:
            logger.error("Local storage delete failed (path=%s): %s", path, exc)
            raise StorageError(f"Failed to delete file: {exc}") from exc


def create_storage_backend() -> StorageBackend:
    """Factory: always returns ``LocalStorageBackend``."""
    logger.info("Storage backend: local (base_path=%s)", settings.local_storage_path)
    return LocalStorageBackend(base_path=settings.local_storage_path)


_storage: StorageBackend | None = None


def _get_storage() -> StorageBackend:
    """Return the singleton storage backend, creating it on first call."""
    global _storage
    if _storage is None:
        _storage = create_storage_backend()
    return _storage


def compute_checksum(content: bytes) -> str:
    """Compute the SHA-256 hex digest of *content*."""
    return hashlib.sha256(content).hexdigest()
