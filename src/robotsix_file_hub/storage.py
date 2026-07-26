"""Storage backend abstraction (S3/MinIO via boto3, or local filesystem)."""

import asyncio
import hashlib
from abc import ABC, abstractmethod
from pathlib import Path

import boto3  # type: ignore[import-untyped]

from .config import Settings

settings = Settings()


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
    def __init__(self, base_path: str) -> None:
        self.base_path = Path(base_path)

    async def save(self, file_id: str, content: bytes) -> str:
        self.base_path.mkdir(parents=True, exist_ok=True)
        file_path = self.base_path / file_id

        def _write() -> None:
            file_path.write_bytes(content)

        try:
            await asyncio.to_thread(_write)
        except OSError as exc:
            raise StorageError(f"Failed to write file: {exc}") from exc

        return str(file_path)

    async def get(self, path: str) -> bytes:
        file_path = Path(path)

        def _read() -> bytes:
            return file_path.read_bytes()

        try:
            return await asyncio.to_thread(_read)
        except OSError as exc:
            raise StorageError(f"Failed to read file: {exc}") from exc

    async def delete(self, path: str) -> None:
        file_path = Path(path)

        def _remove() -> None:
            file_path.unlink(missing_ok=True)

        try:
            await asyncio.to_thread(_remove)
        except OSError as exc:
            raise StorageError(f"Failed to delete file: {exc}") from exc


class S3StorageBackend(StorageBackend):
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
        def _put() -> None:
            self.client.put_object(
                Bucket=self.bucket,
                Key=file_id,
                Body=content,
            )

        try:
            await asyncio.to_thread(_put)
        except Exception as exc:
            raise StorageError(f"Failed to upload to S3: {exc}") from exc

        return f"s3://{self.bucket}/{file_id}"

    async def get(self, path: str) -> bytes:
        def _get() -> bytes:
            key = path.removeprefix(f"s3://{self.bucket}/")
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return bytes(response["Body"].read())

        try:
            return await asyncio.to_thread(_get)
        except Exception as exc:
            raise StorageError(f"Failed to download from S3: {exc}") from exc

    async def delete(self, path: str) -> None:
        def _remove() -> None:
            key = path.removeprefix(f"s3://{self.bucket}/")
            self.client.delete_object(Bucket=self.bucket, Key=key)

        try:
            await asyncio.to_thread(_remove)
        except Exception as exc:
            raise StorageError(f"Failed to delete from S3: {exc}") from exc


def create_storage_backend() -> StorageBackend:
    """Factory: return the configured storage backend."""
    backend = settings.storage_backend
    if backend == "s3":
        return S3StorageBackend(
            endpoint=settings.s3_endpoint,
            bucket=settings.s3_bucket,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
        )
    return LocalStorageBackend(base_path=settings.local_storage_path)


def compute_checksum(content: bytes) -> str:
    """Compute the SHA-256 hex digest of *content*."""
    return hashlib.sha256(content).hexdigest()
