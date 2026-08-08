"""Unit tests for storage backend abstractions (S3, local) and utilities."""

import tempfile
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from src.robotsix_file_hub.config import Settings
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
        # s3_secret_key is a SecretStr on the real model
        mock_settings.s3_secret_key = SecretStr("sk")
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


# ---------------------------------------------------------------------------
# S3StorageBackend class-based tests (from PR)
# ---------------------------------------------------------------------------


class TestS3StorageBackend:
    """Tests for S3StorageBackend with a mocked boto3 client."""

    @pytest.fixture
    def mock_boto3(self) -> Generator[MagicMock]:
        """Patch boto3.client and return the mock client."""
        with patch("src.robotsix_file_hub.storage.boto3.client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client
            yield mock_client

    @pytest.fixture
    def backend(self, mock_boto3: MagicMock) -> S3StorageBackend:
        """Return an S3StorageBackend with a canned config."""
        return S3StorageBackend(
            endpoint="http://localhost:9000",
            bucket="test-bucket",
            access_key="fake-access",
            secret_key="fake-secret",
            region="us-east-1",
        )

    # -- save -----------------------------------------------------------------

    async def test_save_returns_s3_uri(self, backend: S3StorageBackend) -> None:
        """save() returns an s3:// URI and calls put_object."""
        result = await backend.save("abc123", b"content")

        assert result == "s3://test-bucket/abc123"
        backend.client.put_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="abc123",
            Body=b"content",
        )

    async def test_save_maps_exception_to_storage_error(self, backend: S3StorageBackend) -> None:
        """save() wraps put_object failures in StorageError."""
        backend.client.put_object.side_effect = RuntimeError("bucket gone")

        with pytest.raises(StorageError, match="Failed to upload to S3"):
            await backend.save("abc123", b"content")

    # -- get ------------------------------------------------------------------

    async def test_get_fetches_bytes(self, backend: S3StorageBackend) -> None:
        """get() returns bytes from the S3 object body."""
        mock_body = MagicMock()
        mock_body.read.return_value = b"stored content"
        backend.client.get_object.return_value = {"Body": mock_body}

        result = await backend.get("s3://test-bucket/abc123")

        assert result == b"stored content"
        backend.client.get_object.assert_called_once_with(Bucket="test-bucket", Key="abc123")

    async def test_get_strips_s3_prefix(self, backend: S3StorageBackend) -> None:
        """get() strips the s3://<bucket>/ prefix when calling get_object."""
        mock_body = MagicMock()
        mock_body.read.return_value = b"data"
        backend.client.get_object.return_value = {"Body": mock_body}

        await backend.get("s3://test-bucket/nested/path/file.txt")

        # The Key passed to S3 is just the suffix after the bucket prefix.
        backend.client.get_object.assert_called_once_with(
            Bucket="test-bucket", Key="nested/path/file.txt"
        )

    async def test_get_maps_exception_to_storage_error(self, backend: S3StorageBackend) -> None:
        """get() wraps get_object failures in StorageError."""
        backend.client.get_object.side_effect = RuntimeError("not found")

        with pytest.raises(StorageError, match="Failed to download from S3"):
            await backend.get("s3://test-bucket/abc123")

    # -- delete ---------------------------------------------------------------

    async def test_delete_calls_delete_object(self, backend: S3StorageBackend) -> None:
        """delete() calls delete_object with the correct key."""
        await backend.delete("s3://test-bucket/abc123")

        backend.client.delete_object.assert_called_once_with(Bucket="test-bucket", Key="abc123")

    async def test_delete_strips_s3_prefix(self, backend: S3StorageBackend) -> None:
        """delete() strips the s3://<bucket>/ prefix correctly."""
        await backend.delete("s3://test-bucket/nested/file.txt")

        backend.client.delete_object.assert_called_once_with(
            Bucket="test-bucket", Key="nested/file.txt"
        )

    async def test_delete_maps_exception_to_storage_error(self, backend: S3StorageBackend) -> None:
        """delete() wraps delete_object failures in StorageError."""
        backend.client.delete_object.side_effect = RuntimeError("permission denied")

        with pytest.raises(StorageError, match="Failed to delete from S3"):
            await backend.delete("s3://test-bucket/abc123")

    # -- __init__ -------------------------------------------------------------

    def test_init_constructs_client(self) -> None:
        """__init__ calls boto3.client with expected arguments."""
        with patch("src.robotsix_file_hub.storage.boto3.client") as mock_client_factory:
            S3StorageBackend(
                endpoint="http://minio:9000",
                bucket="my-bucket",
                access_key="AK",
                secret_key="SK",
                region="eu-west-1",
            )

        mock_client_factory.assert_called_once_with(
            "s3",
            endpoint_url="http://minio:9000",
            aws_access_key_id="AK",
            aws_secret_access_key="SK",
            region_name="eu-west-1",
        )

    def test_init_empty_endpoint_passes_none(self) -> None:
        """__init__ passes None for endpoint_url when endpoint is empty."""
        with patch("src.robotsix_file_hub.storage.boto3.client") as mock_client_factory:
            S3StorageBackend(
                endpoint="",
                bucket="my-bucket",
                access_key="AK",
                secret_key="SK",
                region="us-east-1",
            )

        mock_client_factory.assert_called_once_with(
            "s3",
            endpoint_url=None,
            aws_access_key_id="AK",
            aws_secret_access_key="SK",
            region_name="us-east-1",
        )


class TestCreateStorageBackend:
    """Tests for create_storage_backend factory."""

    def test_returns_s3_backend_when_configured(self) -> None:
        """Returns S3StorageBackend when storage_backend == 's3'."""
        s3_settings = Settings(
            storage_backend="s3",
            s3_endpoint="http://s3.local",
            s3_bucket="the-bucket",
            s3_access_key="key",
            s3_secret_key="secret",
            s3_region="us-west-2",
        )
        with (
            patch("src.robotsix_file_hub.storage.settings", s3_settings),
            patch("src.robotsix_file_hub.storage.boto3.client") as mock_client_factory,
        ):
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client

            backend = create_storage_backend()

        assert isinstance(backend, S3StorageBackend)
        assert backend.bucket == "the-bucket"
        mock_client_factory.assert_called_once_with(
            "s3",
            endpoint_url="http://s3.local",
            aws_access_key_id="key",
            aws_secret_access_key="secret",
            region_name="us-west-2",
        )

    def test_returns_local_backend_by_default(self) -> None:
        """Returns LocalStorageBackend when storage_backend is not 's3'."""
        from src.robotsix_file_hub.storage import LocalStorageBackend

        local_settings = Settings(
            storage_backend="local",
            local_storage_path="/tmp/uploads",
        )
        with patch("src.robotsix_file_hub.storage.settings", local_settings):
            backend = create_storage_backend()

        assert isinstance(backend, LocalStorageBackend)
        assert str(backend.base_path) == "/tmp/uploads"


class TestComputeChecksum:
    """Tests for compute_checksum."""

    def test_returns_sha256_hex(self) -> None:
        """Returns the SHA-256 hex digest of the input bytes."""
        result = compute_checksum(b"hello")
        expected = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        assert result == expected

    def test_empty_bytes(self) -> None:
        """Returns the correct digest for empty bytes."""
        result = compute_checksum(b"")
        expected = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        assert result == expected

    def test_different_content_different_digest(self) -> None:
        """Different input produces different digests."""
        a = compute_checksum(b"hello")
        b = compute_checksum(b"world")
        assert a != b
        assert len(a) == 64
        assert len(b) == 64
