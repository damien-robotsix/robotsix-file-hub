"""Tests for file upload endpoints."""

import io
import json

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.robotsix_file_hub.models import FileRecord
from src.robotsix_file_hub.storage import StorageBackend


async def test_upload_single_file(test_client: AsyncClient) -> None:
    """POST /files with a single file returns metadata."""
    content = b"hello world"
    response = await test_client.post(
        "/files",
        files={"file": ("test.txt", io.BytesIO(content), "text/plain")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "test.txt"
    assert data["size"] == len(content)
    assert data["content_type"] == "text/plain"
    assert len(data["checksum"]) == 64
    assert "id" in data
    assert "created_at" in data


async def test_upload_with_metadata_persists_and_exposes(
    test_client: AsyncClient,
    test_session_factory: async_sessionmaker,
) -> None:
    """POST /files with a ``metadata`` form field persists the payload.

    The supplied context/tags/provenance must be stored on the record and
    exposed on the metadata read endpoint.
    """
    metadata = {
        "context": "Extracted from Old_structure.stl.zip mail attachment",
        "tags": ["cad", "structure"],
        "provenance": {
            "source_component": "robotsix-auto-mail",
            "mail_subject": "New structure export",
            "container_zip": "Old_structure.stl.zip",
        },
    }
    response = await test_client.post(
        "/files",
        files={"file": ("opaque.stl", io.BytesIO(b"solid model"), "application/octet-stream")},
        data={"metadata": json.dumps(metadata)},
    )
    assert response.status_code == 200
    file_id = response.json()["id"]

    detail = await test_client.get(f"/files/{file_id}/metadata")
    assert detail.status_code == 200
    body = detail.json()
    assert body["upload_metadata"]["context"] == metadata["context"]
    assert body["upload_metadata"]["tags"] == ["cad", "structure"]
    assert body["upload_metadata"]["provenance"]["container_zip"] == "Old_structure.stl.zip"

    # Persisted verbatim on the record as a JSON string.
    async with test_session_factory() as session:
        record = await session.get(FileRecord, file_id)
        assert record is not None
        assert record.upload_metadata is not None
        stored = json.loads(record.upload_metadata)
        assert stored["provenance"]["source_component"] == "robotsix-auto-mail"


async def test_upload_metadata_accepts_source_alias(
    test_client: AsyncClient,
) -> None:
    """The provenance map is also accepted under the ``source`` alias."""
    metadata = {"source": {"source_component": "robotsix-auto-mail"}}
    response = await test_client.post(
        "/files",
        files={"file": ("x.txt", io.BytesIO(b"hello"), "text/plain")},
        data={"metadata": json.dumps(metadata)},
    )
    assert response.status_code == 200
    file_id = response.json()["id"]

    detail = await test_client.get(f"/files/{file_id}/metadata")
    assert detail.status_code == 200
    assert detail.json()["upload_metadata"]["provenance"] == {
        "source_component": "robotsix-auto-mail"
    }


async def test_upload_without_metadata_is_backward_compatible(
    test_client: AsyncClient,
) -> None:
    """Omitting ``metadata`` leaves ``upload_metadata`` null on read."""
    response = await test_client.post(
        "/files",
        files={"file": ("plain.txt", io.BytesIO(b"data"), "text/plain")},
    )
    assert response.status_code == 200
    file_id = response.json()["id"]

    detail = await test_client.get(f"/files/{file_id}/metadata")
    assert detail.status_code == 200
    assert detail.json()["upload_metadata"] is None


async def test_upload_invalid_metadata_returns_400(
    test_client: AsyncClient,
) -> None:
    """A malformed ``metadata`` payload is rejected with 400."""
    response = await test_client.post(
        "/files",
        files={"file": ("plain.txt", io.BytesIO(b"data"), "text/plain")},
        data={"metadata": json.dumps({"tags": "not-a-list"})},
    )
    assert response.status_code == 400


async def test_upload_batch_with_metadata(
    test_client: AsyncClient,
) -> None:
    """POST /files/batch accepts a JSON array of per-file metadata."""
    files = [
        ("files", ("a.txt", io.BytesIO(b"aaa"), "text/plain")),
        ("files", ("b.txt", io.BytesIO(b"bbbbb"), "text/plain")),
    ]
    metadata = [{"context": "first"}, None]
    response = await test_client.post(
        "/files/batch",
        files=files,
        data={"metadata": json.dumps(metadata)},
    )
    assert response.status_code == 200
    ids = [f["id"] for f in response.json()["files"]]

    first = (await test_client.get(f"/files/{ids[0]}/metadata")).json()
    second = (await test_client.get(f"/files/{ids[1]}/metadata")).json()
    assert first["upload_metadata"]["context"] == "first"
    assert second["upload_metadata"] is None


async def test_upload_file_too_large(test_client: AsyncClient) -> None:
    """POST /files with a file exceeding max size returns 413."""
    import src.robotsix_file_hub.routes.files as routes_module

    original_max = routes_module.MAX_FILE_SIZE
    routes_module.MAX_FILE_SIZE = 5

    content = b"this is too large"
    response = await test_client.post(
        "/files",
        files={"file": ("big.txt", io.BytesIO(content), "text/plain")},
    )

    routes_module.MAX_FILE_SIZE = original_max

    assert response.status_code == 413


async def test_upload_batch(test_client: AsyncClient) -> None:
    """POST /files/batch with multiple files returns all metadata."""
    files = [
        ("files", ("a.txt", io.BytesIO(b"aaa"), "text/plain")),
        ("files", ("b.txt", io.BytesIO(b"bbbbb"), "text/plain")),
    ]
    response = await test_client.post("/files/batch", files=files)

    assert response.status_code == 200
    data = response.json()
    assert len(data["files"]) == 2
    assert data["files"][0]["filename"] == "a.txt"
    assert data["files"][0]["size"] == 3
    assert data["files"][1]["filename"] == "b.txt"
    assert data["files"][1]["size"] == 5


async def test_upload_batch_partial_failure_rolls_back(
    test_client: AsyncClient,
    test_session_factory: async_sessionmaker,
    test_storage: StorageBackend,
) -> None:
    """A mid-batch failure rolls back all prior files in the batch.

    When one file in a batch exceeds ``MAX_FILE_SIZE``, every file
    that was already processed must be rolled back — no database
    records and no stored bytes may remain.
    """
    import src.robotsix_file_hub.routes.files as routes_module

    original_max = routes_module.MAX_FILE_SIZE
    routes_module.MAX_FILE_SIZE = 5  # only very small files are accepted

    # First file is small (3 bytes → OK), second is too large (7 bytes → 413).
    files = [
        ("files", ("ok.txt", io.BytesIO(b"abc"), "text/plain")),
        ("files", ("big.txt", io.BytesIO(b"too big"), "text/plain")),
    ]
    response = await test_client.post("/files/batch", files=files)

    routes_module.MAX_FILE_SIZE = original_max

    # The batch must fail.
    assert response.status_code == 413

    # --- Verify no DB records remain ---
    async with test_session_factory() as session:
        result = await session.execute(select(FileRecord))
        rows = result.scalars().all()
        assert len(rows) == 0, f"Expected 0 DB rows after rollback, got {len(rows)}"

    # --- Verify no storage files remain (ignore the test database) ---
    stored = [p for p in test_storage.base_path.iterdir() if p.suffix != ".db"]
    assert len(stored) == 0, f"Expected 0 stored files after rollback, got {len(stored)}"


# ---------------------------------------------------------------------------
# Deduplication tests
# ---------------------------------------------------------------------------


async def test_upload_duplicate_returns_existing_id(
    test_client: AsyncClient,
    test_session_factory: async_sessionmaker,
) -> None:
    """Uploading identical content twice returns the same id on the second call.

    The second upload must return HTTP 200 with ``deduplicated=True`` and
    the same ``id`` as the first upload.  Only one DB record should exist.
    """
    content = b"duplicate content for dedup test"

    # First upload — stores the file.
    resp1 = await test_client.post(
        "/files",
        files={"file": ("first.txt", io.BytesIO(content), "text/plain")},
    )
    assert resp1.status_code == 200
    first = resp1.json()
    assert first["deduplicated"] is False

    # Second upload — same content, different filename.
    resp2 = await test_client.post(
        "/files",
        files={"file": ("second.txt", io.BytesIO(content), "text/plain")},
    )
    assert resp2.status_code == 200
    second = resp2.json()
    assert second["deduplicated"] is True
    assert second["id"] == first["id"]
    assert second["checksum"] == first["checksum"]

    # Only one DB record should exist.
    async with test_session_factory() as session:
        result = await session.execute(select(FileRecord))
        rows = result.scalars().all()
        assert len(rows) == 1


async def test_upload_different_content_same_filename_stores_separately(
    test_client: AsyncClient,
    test_session_factory: async_sessionmaker,
) -> None:
    """Different content under the same filename creates two distinct records."""
    resp1 = await test_client.post(
        "/files",
        files={"file": ("report.pdf", io.BytesIO(b"version A"), "application/pdf")},
    )
    assert resp1.status_code == 200
    first = resp1.json()

    resp2 = await test_client.post(
        "/files",
        files={"file": ("report.pdf", io.BytesIO(b"version B"), "application/pdf")},
    )
    assert resp2.status_code == 200
    second = resp2.json()

    assert first["id"] != second["id"]
    assert first["checksum"] != second["checksum"]

    async with test_session_factory() as session:
        result = await session.execute(select(FileRecord))
        rows = result.scalars().all()
        assert len(rows) == 2


async def test_upload_allow_duplicate_bypasses_dedup(
    test_client: AsyncClient,
    test_session_factory: async_sessionmaker,
) -> None:
    """``?allow_duplicate=true`` stores a second copy even for identical content."""
    content = b"intentional duplicate"

    resp1 = await test_client.post(
        "/files",
        files={"file": ("orig.txt", io.BytesIO(content), "text/plain")},
    )
    assert resp1.status_code == 200
    first = resp1.json()

    resp2 = await test_client.post(
        "/files?allow_duplicate=true",
        files={"file": ("orig.txt", io.BytesIO(content), "text/plain")},
    )
    assert resp2.status_code == 200
    second = resp2.json()

    # Two distinct records despite identical content.
    assert first["id"] != second["id"]
    assert second["deduplicated"] is False

    async with test_session_factory() as session:
        result = await session.execute(select(FileRecord))
        rows = result.scalars().all()
        assert len(rows) == 2


async def test_batch_upload_deduplicates(
    test_client: AsyncClient,
    test_session_factory: async_sessionmaker,
) -> None:
    """Batch upload deduplicates files with identical content."""
    content = b"shared batch content"

    # First, upload one file to establish the checksum.
    resp1 = await test_client.post(
        "/files",
        files={"file": ("batch_a.txt", io.BytesIO(content), "text/plain")},
    )
    assert resp1.status_code == 200
    original_id = resp1.json()["id"]

    # Now batch-upload two files: one new, one duplicate of the first.
    files = [
        ("files", ("batch_b.txt", io.BytesIO(b"totally different"), "text/plain")),
        ("files", ("batch_c.txt", io.BytesIO(content), "text/plain")),
    ]
    resp2 = await test_client.post("/files/batch", files=files)
    assert resp2.status_code == 200
    batch_data = resp2.json()
    assert len(batch_data["files"]) == 2

    new_file = batch_data["files"][0]
    dup_file = batch_data["files"][1]

    assert new_file["deduplicated"] is False
    assert dup_file["deduplicated"] is True
    assert dup_file["id"] == original_id

    # Total DB records: original + batch_b (batch_c was dedup'd).
    async with test_session_factory() as session:
        result = await session.execute(select(FileRecord))
        rows = result.scalars().all()
        assert len(rows) == 2
