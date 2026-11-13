"""Tests for file upload endpoints."""

import io
import json
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.datastructures import UploadFile

from src.robotsix_file_hub.models import FileRecord
from src.robotsix_file_hub.routes import errors as errors_module
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
    import src.robotsix_file_hub.routes.upload as routes_module

    original_max = routes_module.MAX_FILE_SIZE
    routes_module.MAX_FILE_SIZE = 5

    content = b"this is too large"
    response = await test_client.post(
        "/files",
        files={"file": ("big.txt", io.BytesIO(content), "text/plain")},
    )

    routes_module.MAX_FILE_SIZE = original_max

    assert response.status_code == 413


async def test_upload_too_large_stops_reading_early(
    test_client: AsyncClient,
    test_storage: StorageBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An oversized upload is rejected mid-stream without being stored.

    The body is read in bounded chunks: reading stops once the running
    byte count passes ``MAX_FILE_SIZE``, and nothing is written to storage.
    """
    import src.robotsix_file_hub.routes.upload as routes_module

    monkeypatch.setattr(routes_module, "MAX_FILE_SIZE", 10)
    monkeypatch.setattr(routes_module, "STREAM_CHUNK_SIZE", 4)

    read_sizes: list[int] = []
    original_read = UploadFile.read

    async def _tracking_read(self: UploadFile, size: int = -1) -> bytes:
        read_sizes.append(size)
        return await original_read(self, size)

    async def _forbidden_save(*args: object, **kwargs: object) -> str:
        raise AssertionError("storage must not be written for oversized uploads")

    monkeypatch.setattr(UploadFile, "read", _tracking_read)
    monkeypatch.setattr(type(test_storage), "save", _forbidden_save)
    monkeypatch.setattr(type(test_storage), "save_stream", _forbidden_save)

    response = await test_client.post(
        "/files",
        files={"file": ("big.bin", io.BytesIO(b"x" * 1000), "application/octet-stream")},
    )

    assert response.status_code == 413
    # Never an unbounded read, and stopped just past the limit (3 x 4 bytes).
    assert read_sizes
    assert all(size == 4 for size in read_sizes)
    assert len(read_sizes) == 3
    assert _stored_blobs(test_storage) == []


@pytest.mark.parametrize(
    ("size", "expected_status"),
    [(10, 200), (11, 413)],
    ids=["exactly-max-accepted", "one-byte-over-rejected"],
)
async def test_upload_size_limit_boundary(
    test_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    size: int,
    expected_status: int,
) -> None:
    """A body of exactly ``MAX_FILE_SIZE`` is accepted; one byte more is 413.

    The chunk size does not divide the limit evenly, so the boundary is
    checked against a partial final chunk.
    """
    import src.robotsix_file_hub.routes.upload as routes_module

    monkeypatch.setattr(routes_module, "MAX_FILE_SIZE", 10)
    monkeypatch.setattr(routes_module, "STREAM_CHUNK_SIZE", 4)

    response = await test_client.post(
        "/files",
        files={"file": ("edge.bin", io.BytesIO(b"x" * size), "application/octet-stream")},
    )

    assert response.status_code == expected_status
    if expected_status == 200:
        assert response.json()["size"] == size


@pytest.mark.parametrize(
    ("size", "expected_status"),
    [(10, 200), (11, 413)],
    ids=["exactly-max-accepted", "one-byte-over-rejected"],
)
async def test_upload_batch_size_limit_boundary(
    test_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    size: int,
    expected_status: int,
) -> None:
    """The batch endpoint applies the same per-file inclusive size limit."""
    import src.robotsix_file_hub.routes.upload as routes_module

    monkeypatch.setattr(routes_module, "MAX_FILE_SIZE", 10)
    monkeypatch.setattr(routes_module, "STREAM_CHUNK_SIZE", 4)

    files = [
        ("files", ("small.bin", io.BytesIO(b"abc"), "application/octet-stream")),
        ("files", ("edge.bin", io.BytesIO(b"y" * size), "application/octet-stream")),
    ]
    response = await test_client.post("/files/batch", files=files)

    assert response.status_code == expected_status
    if expected_status == 200:
        assert [f["size"] for f in response.json()["files"]] == [3, size]


def _chunked_multipart(
    payload: bytes,
    *,
    parts: list[tuple[str, str, bytes]] | None = None,
) -> tuple[dict[str, str], AsyncIterator[bytes]]:
    """Build a multipart body streamed without a ``Content-Length`` header.

    By default the body holds a single ``file`` part carrying *payload*.
    Pass *parts* as ``(field, filename, payload)`` tuples to send several
    file parts instead (e.g. for ``POST /files/batch``).

    Returns ``(headers, content)`` where *content* is an async generator,
    so httpx sends it with ``Transfer-Encoding: chunked`` (unknown length).
    """
    boundary = "testboundary7c3f"
    if parts is None:
        parts = [("file", "stream.bin", payload)]

    def _head(field: str, filename: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()

    tail = f"--{boundary}--\r\n".encode()

    async def _body() -> AsyncIterator[bytes]:
        for field, filename, data in parts:
            yield _head(field, filename)
            for i in range(0, len(data), 3):
                yield data[i : i + 3]
            yield b"\r\n"
        yield tail

    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    return headers, _body()


@pytest.mark.parametrize(
    ("size", "expected_status"),
    [(10, 200), (11, 413)],
    ids=["exactly-max-accepted", "one-byte-over-rejected"],
)
async def test_upload_chunked_unknown_length_body(
    test_client: AsyncClient,
    test_storage: StorageBackend,
    monkeypatch: pytest.MonkeyPatch,
    size: int,
    expected_status: int,
) -> None:
    """A chunked body with no ``Content-Length`` is still size-checked."""
    import src.robotsix_file_hub.routes.upload as routes_module

    monkeypatch.setattr(routes_module, "MAX_FILE_SIZE", 10)
    monkeypatch.setattr(routes_module, "STREAM_CHUNK_SIZE", 4)

    payload = b"z" * size
    headers, content = _chunked_multipart(payload)
    response = await test_client.post("/files", headers=headers, content=content)

    assert response.status_code == expected_status
    if expected_status == 200:
        data = response.json()
        assert data["size"] == size
        download = await test_client.get(f"/files/{data['id']}")
        assert download.content == payload
    else:
        assert _stored_blobs(test_storage) == []


@pytest.mark.parametrize(
    ("size", "expected_status"),
    [(10, 200), (11, 413)],
    ids=["exactly-max-accepted", "one-byte-over-rejected"],
)
async def test_upload_batch_chunked_unknown_length_body(
    test_client: AsyncClient,
    test_session_factory: async_sessionmaker,
    test_storage: StorageBackend,
    monkeypatch: pytest.MonkeyPatch,
    size: int,
    expected_status: int,
) -> None:
    """A chunked batch body with no ``Content-Length`` is size-checked per file.

    An oversized file rolls back the whole batch (no DB rows, no blobs);
    a file of exactly ``MAX_FILE_SIZE`` is accepted.
    """
    import src.robotsix_file_hub.routes.upload as routes_module

    monkeypatch.setattr(routes_module, "MAX_FILE_SIZE", 10)
    monkeypatch.setattr(routes_module, "STREAM_CHUNK_SIZE", 4)

    small = b"abc"
    edge = b"y" * size
    headers, content = _chunked_multipart(
        b"",
        parts=[("files", "small.bin", small), ("files", "edge.bin", edge)],
    )
    response = await test_client.post("/files/batch", headers=headers, content=content)

    assert response.status_code == expected_status
    if expected_status == 200:
        files = response.json()["files"]
        assert [f["size"] for f in files] == [len(small), size]
        download = await test_client.get(f"/files/{files[1]['id']}")
        assert download.content == edge
    else:
        async with test_session_factory() as session:
            rows = (await session.execute(select(FileRecord))).scalars().all()
            assert rows == []
        assert _stored_blobs(test_storage) == []


async def test_upload_streamed_content_roundtrips(test_client: AsyncClient) -> None:
    """A multi-chunk upload is stored intact with a correct checksum."""
    import hashlib

    import src.robotsix_file_hub.routes.upload as routes_module

    content = bytes(range(256)) * 50
    original_chunk = routes_module.STREAM_CHUNK_SIZE
    routes_module.STREAM_CHUNK_SIZE = 1000
    try:
        response = await test_client.post(
            "/files",
            files={"file": ("blob.bin", io.BytesIO(content), "application/octet-stream")},
        )
    finally:
        routes_module.STREAM_CHUNK_SIZE = original_chunk

    assert response.status_code == 200
    data = response.json()
    assert data["size"] == len(content)
    assert data["checksum"] == hashlib.sha256(content).hexdigest()

    download = await test_client.get(f"/files/{data['id']}")
    assert download.status_code == 200
    assert download.content == content


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
    import src.robotsix_file_hub.routes.upload as routes_module

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


def _force_commit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every ``AsyncSession.commit`` raise to simulate a DB failure."""

    async def _failing_commit(self: AsyncSession) -> None:
        raise RuntimeError("simulated commit failure")

    monkeypatch.setattr(AsyncSession, "commit", _failing_commit)


def _stored_blobs(storage: StorageBackend) -> list:
    """Stored blob files, ignoring the SQLite test database files."""
    return [p for p in storage.base_path.iterdir() if not p.name.startswith("test.db")]


async def test_upload_single_commit_failure_cleans_up_storage(
    test_client: AsyncClient,
    test_session_factory: async_sessionmaker,
    test_storage: StorageBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing ``db.commit()`` on single upload deletes the stored blob."""
    _force_commit_failure(monkeypatch)

    response = await test_client.post(
        "/files",
        files={"file": ("orphan.txt", io.BytesIO(b"orphan bytes"), "text/plain")},
    )

    monkeypatch.undo()

    assert response.status_code == 500
    assert "Database failure" in response.json()["detail"]

    async with test_session_factory() as session:
        rows = (await session.execute(select(FileRecord))).scalars().all()
        assert rows == []

    assert _stored_blobs(test_storage) == []


async def test_upload_batch_commit_failure_cleans_up_storage(
    test_client: AsyncClient,
    test_session_factory: async_sessionmaker,
    test_storage: StorageBackend,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing ``db.commit()`` on batch upload deletes every stored blob."""
    _force_commit_failure(monkeypatch)

    files = [
        ("files", ("a.txt", io.BytesIO(b"aaa"), "text/plain")),
        ("files", ("b.txt", io.BytesIO(b"bbbbb"), "text/plain")),
    ]
    response = await test_client.post("/files/batch", files=files)

    monkeypatch.undo()

    assert response.status_code == 500
    assert "Database failure" in response.json()["detail"]

    async with test_session_factory() as session:
        rows = (await session.execute(select(FileRecord))).scalars().all()
        assert rows == []

    assert _stored_blobs(test_storage) == []


async def test_upload_db_failure_does_not_leak_exception_text(
    test_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A DB exception message is logged server-side but never sent to the client."""
    secret = "INSERT INTO files_records host=db.internal:5432 password=hunter2"

    async def _failing_commit(self: AsyncSession) -> None:
        raise RuntimeError(secret)

    monkeypatch.setattr(AsyncSession, "commit", _failing_commit)

    errors_logger = errors_module.logger.name
    with caplog.at_level("ERROR", logger=errors_logger):
        response = await test_client.post(
            "/files",
            files={"file": ("leak.txt", io.BytesIO(b"leak bytes"), "text/plain")},
        )

    monkeypatch.undo()

    assert response.status_code == 500
    assert response.json()["detail"] == "Database failure"
    assert "hunter2" not in response.text
    assert "db.internal" not in response.text
    logged = [r for r in caplog.records if r.name == errors_logger]
    assert any(r.exc_info and str(r.exc_info[1]) == secret for r in logged)


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
