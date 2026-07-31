"""Unit tests for storage backend abstractions."""

import tempfile
from unittest.mock import MagicMock, patch

import pytest

from src.robotsix_file_hub.storage import (
    LocalStorageBackend,
    S3StorageBackend,
    StorageError,
    compute_checksum,
    create_storage_backend,
)

# ---------------------------------------------------------------------------
# compute_checksum
# ---------------------------------------------------------------------------


def test_compute_checksum_deterministic() -> None:
    """Same input always produces the same hex digest."""
    content = b"hello world"
    assert compute_checksum(content) == compute_checksum(content)


def test_compute_checksum_distinct_inputs() -> None:
    """Different inputs produce different hex digests."""
    digest_a = compute_checksum(b"alpha")
    digest_b = compute_checksum(b"beta")
    assert digest_a != digest_b


def test_compute_checksum_empty_content() -> None:
    """Empty bytes produce the well-known empty SHA-256 digest."""
    assert (
        compute_checksum(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_compute_checksum_length() -> None:
    """Digest is 64 hex characters (256 bits)."""
    assert len(compute_checksum(b"anything")) == 64


# ---------------------------------------------------------------------------
# create_storage_backend factory
# ---------------------------------------------------------------------------


def test_create_storage_backend_default_is_local() -> None:
    """With default settings (storage_backend='local'), returns LocalStorageBackend."""
    backend = create_storage_backend()
    assert isinstance(backend, LocalStorageBackend)


def test_create_storage_backend_local() -> None:
    """Explicit 'local' returns LocalStorageBackend."""
    with patch("src.robotsix_file_hub.storage.settings") as mock_settings:
        mock_settings.storage_backend = "local"
        mock_settings.local_storage_path = "/tmp/test"
        backend = create_storage_backend()
        assert isinstance(backend, LocalStorageBackend)


def test_create_storage_backend_s3() -> None:
    """When storage_backend='s3', returns S3StorageBackend."""
    with (
        patch("src.robotsix_file_hub.storage.settings") as mock_settings,
        patch("src.robotsix_file_hub.storage.boto3.client") as mock_boto3_client,
    ):
        mock_settings.storage_backend = "s3"
        mock_settings.s3_endpoint = "http://minio:9000"
        mock_settings.s3_bucket = "my-bucket"
        mock_settings.s3_access_key = "ak"
        mock_settings.s3_secret_key = "sk"
        mock_settings.s3_region = "us-east-1"
        mock_boto3_client.return_value = MagicMock()

        backend = create_storage_backend()
        assert isinstance(backend, S3StorageBackend)


# ---------------------------------------------------------------------------
# LocalStorageBackend direct unit tests
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir() -> str:
    """Temporary directory for LocalStorageBackend tests."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def local_backend(tmp_dir: str) -> LocalStorageBackend:
    """LocalStorageBackend pointing at the temp directory."""
    return LocalStorageBackend(base_path=tmp_dir)


async def test_local_save_creates_parent_dirs(tmp_dir: str) -> None:
    """save() creates the base_path directory if it does not exist."""
    import os

    base = os.path.join(tmp_dir, "nested", "sub")
    backend = LocalStorageBackend(base_path=base)
    path = await backend.save("test.txt", b"content")
    assert path.startswith(base)
    assert os.path.isdir(base)


async def test_local_save_and_get_roundtrip(local_backend: LocalStorageBackend) -> None:
    """Data saved can be retrieved with the same bytes."""
    content = b"roundtrip payload"
    path = await local_backend.save("myfile.bin", content)
    result = await local_backend.get(path)
    assert result == content


async def test_local_save_and_delete_roundtrip(local_backend: LocalStorageBackend) -> None:
    """Data saved can be deleted, and subsequent get raises StorageError."""
    path = await local_backend.save("to-delete.bin", b"tmp")
    await local_backend.delete(path)
    with pytest.raises(StorageError):
        await local_backend.get(path)


async def test_local_get_missing_file_raises_storage_error(
    local_backend: LocalStorageBackend,
) -> None:
    """Reading a non-existent path raises StorageError."""
    import os

    fake_path = os.path.join(local_backend.base_path, "does-not-exist")
    with pytest.raises(StorageError):
        await local_backend.get(fake_path)


async def test_local_delete_missing_ok(local_backend: LocalStorageBackend) -> None:
    """Deleting a non-existent path does not raise (missing_ok=True)."""
    import os

    fake_path = os.path.join(local_backend.base_path, "does-not-exist")
    # Should not raise
    await local_backend.delete(fake_path)


async def test_local_delete_then_delete_missing_ok(
    local_backend: LocalStorageBackend,
) -> None:
    """Deleting the same file twice: first succeeds, second is silent."""
    path = await local_backend.save("double-delete.bin", b"data")
    await local_backend.delete(path)
    # Second delete on same path should not raise (missing_ok)
    await local_backend.delete(path)


# ---------------------------------------------------------------------------
# S3StorageBackend unit tests (patched boto3 client)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_boto3_client() -> MagicMock:
    """Return a MagicMock standing in for a boto3 S3 client."""
    return MagicMock()


@pytest.fixture
def s3_backend(mock_boto3_client: MagicMock) -> S3StorageBackend:
    """S3StorageBackend with a patched boto3 client."""
    with patch("src.robotsix_file_hub.storage.boto3.client", return_value=mock_boto3_client):
        backend = S3StorageBackend(
            endpoint="http://minio:9000",
            bucket="test-bucket",
            access_key="ak",
            secret_key="sk",
            region="us-east-1",
        )
    return backend


async def test_s3_save_returns_s3_path(
    s3_backend: S3StorageBackend,
    mock_boto3_client: MagicMock,
) -> None:
    """save() returns an s3:// URL and calls put_object."""
    content = b"hello s3"
    result = await s3_backend.save("my-key", content)
    assert result == "s3://test-bucket/my-key"
    mock_boto3_client.put_object.assert_called_once_with(
        Bucket="test-bucket",
        Key="my-key",
        Body=content,
    )


async def test_s3_get_returns_bytes(
    s3_backend: S3StorageBackend,
    mock_boto3_client: MagicMock,
) -> None:
    """get() returns bytes from the S3 response body."""
    expected = b"retrieved bytes"

    # Build a fake response whose Body.read() returns expected bytes
    fake_body = MagicMock()
    fake_body.read.return_value = expected
    fake_response = {"Body": fake_body}
    mock_boto3_client.get_object.return_value = fake_response

    result = await s3_backend.get("s3://test-bucket/my-key")
    assert result == expected
    mock_boto3_client.get_object.assert_called_once_with(Bucket="test-bucket", Key="my-key")


async def test_s3_get_strips_bucket_prefix(
    s3_backend: S3StorageBackend,
    mock_boto3_client: MagicMock,
) -> None:
    """get() strips the s3://bucket/ prefix before calling get_object."""
    fake_body = MagicMock()
    fake_body.read.return_value = b"data"
    mock_boto3_client.get_object.return_value = {"Body": fake_body}

    await s3_backend.get("s3://test-bucket/nested/path/key.txt")
    mock_boto3_client.get_object.assert_called_once_with(
        Bucket="test-bucket", Key="nested/path/key.txt"
    )


async def test_s3_delete_calls_delete_object(
    s3_backend: S3StorageBackend,
    mock_boto3_client: MagicMock,
) -> None:
    """delete() calls delete_object with the correct bucket and key."""
    await s3_backend.delete("s3://test-bucket/to-delete")
    mock_boto3_client.delete_object.assert_called_once_with(Bucket="test-bucket", Key="to-delete")


async def test_s3_save_wraps_exception_in_storage_error(
    s3_backend: S3StorageBackend,
    mock_boto3_client: MagicMock,
) -> None:
    """When put_object raises, save() wraps it in StorageError."""
    mock_boto3_client.put_object.side_effect = RuntimeError("network down")
    with pytest.raises(StorageError, match="Failed to upload to S3"):
        await s3_backend.save("key", b"content")


async def test_s3_get_wraps_exception_in_storage_error(
    s3_backend: S3StorageBackend,
    mock_boto3_client: MagicMock,
) -> None:
    """When get_object raises, get() wraps it in StorageError."""
    mock_boto3_client.get_object.side_effect = RuntimeError("not found")
    with pytest.raises(StorageError, match="Failed to download from S3"):
        await s3_backend.get("s3://test-bucket/key")


async def test_s3_delete_wraps_exception_in_storage_error(
    s3_backend: S3StorageBackend,
    mock_boto3_client: MagicMock,
) -> None:
    """When delete_object raises, delete() wraps it in StorageError."""
    mock_boto3_client.delete_object.side_effect = RuntimeError("permission denied")
    with pytest.raises(StorageError, match="Failed to delete from S3"):
        await s3_backend.delete("s3://test-bucket/key")
