"""Storage backend abstraction (S3/MinIO via boto3, or local filesystem)."""

import asyncio
import hashlib
import logging
from abc import ABC, abstractmethod
from pathlib import Path

import boto3  # type: ignore[import-untyped]

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


class S3StorageBackend(StorageBackend):
    """MinIO / S3 ``StorageBackend`` via ``boto3``.

    Constructs a ``boto3`` S3 client from *endpoint*, *bucket*,
    *access_key*, *secret_key*, and *region*.  Each public method
    offloads the boto3 call to a thread and wraps exceptions in
    ``StorageError``.  The key prefix ``s3://<bucket>/`` is stripped
    in ``get`` and ``delete``.
    """

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str,
    ) -> None:
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint or None,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    async def save(self, file_id: str, content: bytes) -> str:
        """Upload *content* as object *file_id*, returning its S3 URI.

        Offloads the ``put_object`` call to a thread; raises
        ``StorageError`` on failure.
        """

        def _put() -> None:
            self.client.put_object(
                Bucket=self.bucket,
                Key=file_id,
                Body=content,
            )

        try:
            await asyncio.to_thread(_put)
        except Exception as exc:
            logger.error("S3 upload failed (bucket=%s, key=%s): %s", self.bucket, file_id, exc)
            raise StorageError(f"Failed to upload to S3: {exc}") from exc

        return f"s3://{self.bucket}/{file_id}"

    async def get(self, path: str) -> bytes:
        """Retrieve object bytes by S3 *path*.

        Strips the ``s3://<bucket>/`` prefix, offloads the
        ``get_object`` call to a thread; raises ``StorageError``
        on failure.
        """

        def _get() -> bytes:
            key = path.removeprefix(f"s3://{self.bucket}/")
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return bytes(response["Body"].read())

        try:
            return await asyncio.to_thread(_get)
        except Exception as exc:
            logger.error("S3 download failed (bucket=%s, path=%s): %s", self.bucket, path, exc)
            raise StorageError(f"Failed to download from S3: {exc}") from exc

    async def delete(self, path: str) -> None:
        """Remove the object at S3 *path*.

        Strips the ``s3://<bucket>/`` prefix, offloads the
        ``delete_object`` call to a thread; raises ``StorageError``
        on failure.
        """

        def _remove() -> None:
            key = path.removeprefix(f"s3://{self.bucket}/")
            self.client.delete_object(Bucket=self.bucket, Key=key)

        try:
            await asyncio.to_thread(_remove)
        except Exception as exc:
            # nosec B608 — not SQL. Bandit's hardcoded-SQL heuristic matches the
            # words "delete … from" inside this f-string; it is an error message
            # about an S3 object, and this module issues no queries at all.
            # Flagged Medium/LOW-confidence, which is the shape of a regex hit
            # rather than a finding.
            logger.error("S3 delete failed (bucket=%s, path=%s): %s", self.bucket, path, exc)
            raise StorageError(f"Failed to delete from S3: {exc}") from exc  # nosec B608


def create_storage_backend() -> StorageBackend:
    """Factory: return the configured storage backend."""
    backend = settings.storage_backend
    if backend == "s3":
        logger.info(
            "Storage backend: s3 (bucket=%s, endpoint=%s)",
            settings.s3_bucket,
            settings.s3_endpoint,
        )
        return S3StorageBackend(
            endpoint=settings.s3_endpoint,
            bucket=settings.s3_bucket,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key.get_secret_value(),
            region=settings.s3_region,
        )
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
