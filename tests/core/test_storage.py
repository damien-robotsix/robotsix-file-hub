"""Unit tests for storage backend abstractions (local) and utilities."""

import tempfile
from unittest.mock import patch

import pytest

from src.robotsix_file_hub.config import Settings
from src.robotsix_file_hub.storage import (
    LocalStorageBackend,
    StorageError,
    _get_storage,
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
    """create_storage_backend() returns LocalStorageBackend."""
    backend = create_storage_backend()
    assert isinstance(backend, LocalStorageBackend)


def test_create_storage_backend_local() -> None:
    """create_storage_backend() uses configured local_storage_path."""
    with patch("src.robotsix_file_hub.storage.settings") as mock_settings:
        mock_settings.local_storage_path = "/tmp/test"
        backend = create_storage_backend()
        assert isinstance(backend, LocalStorageBackend)


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


class TestCreateStorageBackend:
    """Tests for create_storage_backend factory."""

    def test_returns_local_backend(self) -> None:
        """create_storage_backend() always returns LocalStorageBackend."""
        local_settings = Settings(
            local_storage_path="/tmp/uploads",
        )
        with patch("src.robotsix_file_hub.storage.settings", local_settings):
            backend = create_storage_backend()

        assert isinstance(backend, LocalStorageBackend)
        assert str(backend.base_path) == "/tmp/uploads"


class TestGetStorage:
    """Tests for the _get_storage() lazy singleton helper."""

    def test_cache_miss_creates_backend(self) -> None:
        """First call creates a StorageBackend and caches it in _storage."""
        import src.robotsix_file_hub.storage as storage_module

        storage_module._storage = None  # reset singleton
        result = _get_storage()
        assert isinstance(result, LocalStorageBackend)
        assert storage_module._storage is result

    def test_cache_hit_returns_same_instance(self) -> None:
        """Second call returns the exact same object (identity check)."""
        import src.robotsix_file_hub.storage as storage_module

        first = _get_storage()
        second = _get_storage()
        assert first is second
        assert storage_module._storage is first

    async def test_concurrent_calls_return_same_instance(self) -> None:
        """Multiple concurrent calls all get the same singleton."""
        import asyncio

        import src.robotsix_file_hub.storage as storage_module

        storage_module._storage = None  # reset singleton

        async def get_in_task() -> object:
            # Simulate a context switch so tasks overlap
            await asyncio.sleep(0)
            return _get_storage()

        # Launch several tasks concurrently
        results = await asyncio.gather(
            get_in_task(),
            get_in_task(),
            get_in_task(),
        )
        # All must be the same instance
        assert results[0] is results[1] is results[2]
        assert storage_module._storage is results[0]


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
