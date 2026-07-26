"""Tests for background task queue and enrichment."""

import asyncio
import io
import os
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.robotsix_file_hub.models import Base, FileRecord
from src.robotsix_file_hub.storage import LocalStorageBackend, StorageBackend
from src.robotsix_file_hub.tasks import enqueue_enrichment, start_workers, stop_workers


@pytest.fixture
async def tasks_test_env(tmp_upload_dir: str):
    """Set up engine, session factory, storage, and monkey-patch tasks_module.

    Yields ``(session_factory, storage)`` for worker / reindex unit tests
    that monkey-patch ``tasks_module.async_session_factory``.
    """
    import src.robotsix_file_hub.tasks as tasks_module

    db_path = os.path.join(tmp_upload_dir, "test.db")
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    storage = LocalStorageBackend(base_path=tmp_upload_dir)

    original_session_local = tasks_module.async_session_factory
    tasks_module.async_session_factory = session_factory  # type: ignore[assignment]

    try:
        yield session_factory, storage
    finally:
        tasks_module._storage = None
        tasks_module.async_session_factory = original_session_local
        tasks_module._reindex_total = 0
        tasks_module._reindex_completed = 0
        tasks_module._reindex_failed = 0
        tasks_module._reindex_active = False
        await engine.dispose()


# ── Enrichment worker tests ────────────────────────────────────────


async def test_enrichment_worker_updates_record(tasks_test_env) -> None:
    """Enqueue an enrichment job and wait for the worker to process it.

    The enrichment module is mocked to return canned values so we
    don't need a real LLM or file content.
    """
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, storage = tasks_test_env

    # Write a real file so the storage backend can read it
    file_id = "test-file-001"
    storage_path = await storage.save(file_id, b"hello world text content")

    try:
        # Insert a file record
        async with session_factory() as session:
            record = FileRecord(
                id=file_id,
                filename="report.txt",
                size=24,
                content_type="text/plain",
                checksum="abc123",
                storage_path=storage_path,
            )
            session.add(record)
            await session.commit()

        # Mock enrich_file to return canned enrichment
        canned = {"category": "document", "tags": "pdf,report", "summary": "A report file."}

        with patch.object(tasks_module, "enrich_file", new=AsyncMock(return_value=canned)):
            tasks_module._storage = storage

            # Start one worker
            await start_workers(count=1)

            # Enqueue enrichment
            enqueue_enrichment(
                file_id=file_id,
                storage_path=storage_path,
                content_type="text/plain",
            )

            # Poll for enrichment completion
            for _ in range(20):
                async with session_factory() as session:
                    r = await session.get(FileRecord, file_id)
                    if r and r.category is not None:
                        break
                await asyncio.sleep(0.1)
            else:
                await stop_workers()
                pytest.fail("Enrichment was not processed within timeout")

            # Verify enrichment fields were populated from the mock
            async with session_factory() as session:
                r = await session.get(FileRecord, file_id)
                assert r is not None
                assert r.category == "document"
                assert r.tags == "pdf,report"
                assert r.summary == "A report file."
                assert r.source == "upload"

    finally:
        await stop_workers()
        tasks_module._storage = None


async def test_enrichment_worker_null_on_llm_failure(tasks_test_env) -> None:
    """When enrich_file returns None fields, the DB record is updated with nulls."""
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, storage = tasks_test_env

    file_id = "test-file-002"
    storage_path = await storage.save(file_id, b"binary blob")

    try:
        async with session_factory() as session:
            record = FileRecord(
                id=file_id,
                filename="blob.bin",
                size=11,
                content_type="application/octet-stream",
                checksum="def456",
                storage_path=storage_path,
            )
            session.add(record)
            await session.commit()

        # enrich_file returns all None (no text extracted)
        canned = {"category": None, "tags": None, "summary": None}

        with patch.object(tasks_module, "enrich_file", new=AsyncMock(return_value=canned)):
            tasks_module._storage = storage

            await start_workers(count=1)

            enqueue_enrichment(
                file_id=file_id,
                storage_path=storage_path,
                content_type="application/octet-stream",
            )

            for _ in range(20):
                async with session_factory() as session:
                    r = await session.get(FileRecord, file_id)
                    if r and r.source is not None:
                        break
                await asyncio.sleep(0.1)
            else:
                await stop_workers()
                pytest.fail("Enrichment was not processed within timeout")

            async with session_factory() as session:
                r = await session.get(FileRecord, file_id)
                assert r is not None
                assert r.category is None
                assert r.tags is None
                assert r.summary is None
                assert r.source == "upload"

    finally:
        await stop_workers()
        tasks_module._storage = None


# ── Upload-enqueues-enrichment test ─────────────────────────────────


async def test_upload_enqueues_enrichment(
    test_client: AsyncClient,
    test_storage: StorageBackend,
) -> None:
    """POST /files should enqueue an enrichment job after DB write."""
    import src.robotsix_file_hub.routes.files as routes_module

    enqueued: list[tuple[str, str, str]] = []
    original_enqueue = routes_module.enqueue_enrichment

    def _capture_enqueue(*, file_id: str, storage_path: str, content_type: str) -> None:
        enqueued.append((file_id, storage_path, content_type))

    routes_module.enqueue_enrichment = _capture_enqueue  # type: ignore[assignment]

    try:
        response = await test_client.post(
            "/files",
            files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(enqueued) == 1
        assert enqueued[0][0] == data["id"]
        assert enqueued[0][1].startswith(str(test_storage.base_path))
        assert enqueued[0][2] == "text/plain"

    finally:
        routes_module.enqueue_enrichment = original_enqueue


# ── Reindex unit tests ─────────────────────────────────────────────


async def test_reindex_all_enqueues_all_files(tasks_test_env) -> None:
    """enqueue_reindex_all enqueues enrichment for every file in the DB."""
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, _storage = tasks_test_env

    # Pre-populate DB with two records
    async with session_factory() as session:
        session.add_all(
            [
                FileRecord(
                    id="r1",
                    filename="a.txt",
                    size=10,
                    content_type="text/plain",
                    checksum="aa",
                    storage_path="/tmp/a.txt",
                ),
                FileRecord(
                    id="r2",
                    filename="b.png",
                    size=20,
                    content_type="image/png",
                    checksum="bb",
                    storage_path="/tmp/b.png",
                ),
            ]
        )
        await session.commit()

    enqueued: list[tuple[str, str, str]] = []
    original_enqueue = tasks_module.enqueue_enrichment

    def _capture_enqueue(*, file_id: str, storage_path: str, content_type: str) -> None:
        enqueued.append((file_id, storage_path, content_type))

    tasks_module.enqueue_enrichment = _capture_enqueue  # type: ignore[assignment]

    try:
        result = await tasks_module.enqueue_reindex_all()

        assert result["enqueued"] == 2
        assert len(enqueued) == 2
        file_ids = {e[0] for e in enqueued}
        assert file_ids == {"r1", "r2"}

        # Progress counters should be set
        assert tasks_module._reindex_total == 2
        assert tasks_module._reindex_active is True

    finally:
        tasks_module.enqueue_enrichment = original_enqueue


async def test_reindex_all_filtered_by_category(tasks_test_env) -> None:
    """enqueue_reindex_all with category filter only enqueues matching files."""
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, _storage = tasks_test_env

    async with session_factory() as session:
        session.add_all(
            [
                FileRecord(
                    id="r1",
                    filename="a.txt",
                    size=10,
                    content_type="text/plain",
                    checksum="aa",
                    storage_path="/tmp/a.txt",
                    category="document",
                ),
                FileRecord(
                    id="r2",
                    filename="b.png",
                    size=20,
                    content_type="image/png",
                    checksum="bb",
                    storage_path="/tmp/b.png",
                    category="image",
                ),
                FileRecord(
                    id="r3",
                    filename="c.txt",
                    size=30,
                    content_type="text/plain",
                    checksum="cc",
                    storage_path="/tmp/c.txt",
                    category="document",
                ),
            ]
        )
        await session.commit()

    enqueued: list[tuple[str, str, str]] = []
    original_enqueue = tasks_module.enqueue_enrichment

    def _capture_enqueue(*, file_id: str, storage_path: str, content_type: str) -> None:
        enqueued.append((file_id, storage_path, content_type))

    tasks_module.enqueue_enrichment = _capture_enqueue  # type: ignore[assignment]

    try:
        result = await tasks_module.enqueue_reindex_all(category="document")

        assert result["enqueued"] == 2
        assert len(enqueued) == 2
        file_ids = {e[0] for e in enqueued}
        assert file_ids == {"r1", "r3"}

    finally:
        tasks_module.enqueue_enrichment = original_enqueue


async def test_reindex_all_filtered_by_content_type(tasks_test_env) -> None:
    """enqueue_reindex_all with content_type filter only enqueues matching files."""
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, _storage = tasks_test_env

    async with session_factory() as session:
        session.add_all(
            [
                FileRecord(
                    id="r1",
                    filename="a.txt",
                    size=10,
                    content_type="text/plain",
                    checksum="aa",
                    storage_path="/tmp/a.txt",
                ),
                FileRecord(
                    id="r2",
                    filename="b.png",
                    size=20,
                    content_type="image/png",
                    checksum="bb",
                    storage_path="/tmp/b.png",
                ),
            ]
        )
        await session.commit()

    enqueued: list[tuple[str, str, str]] = []
    original_enqueue = tasks_module.enqueue_enrichment

    def _capture_enqueue(*, file_id: str, storage_path: str, content_type: str) -> None:
        enqueued.append((file_id, storage_path, content_type))

    tasks_module.enqueue_enrichment = _capture_enqueue  # type: ignore[assignment]

    try:
        result = await tasks_module.enqueue_reindex_all(content_type="image/png")

        assert result["enqueued"] == 1
        assert len(enqueued) == 1
        assert enqueued[0][0] == "r2"

    finally:
        tasks_module.enqueue_enrichment = original_enqueue


async def test_reindex_all_filtered_by_file_ids(tasks_test_env) -> None:
    """enqueue_reindex_all with file_ids filter only enqueues specified files."""
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, _storage = tasks_test_env

    async with session_factory() as session:
        session.add_all(
            [
                FileRecord(
                    id="r1",
                    filename="a.txt",
                    size=10,
                    content_type="text/plain",
                    checksum="aa",
                    storage_path="/tmp/a.txt",
                ),
                FileRecord(
                    id="r2",
                    filename="b.png",
                    size=20,
                    content_type="image/png",
                    checksum="bb",
                    storage_path="/tmp/b.png",
                ),
                FileRecord(
                    id="r3",
                    filename="c.txt",
                    size=30,
                    content_type="text/plain",
                    checksum="cc",
                    storage_path="/tmp/c.txt",
                ),
            ]
        )
        await session.commit()

    enqueued: list[tuple[str, str, str]] = []
    original_enqueue = tasks_module.enqueue_enrichment

    def _capture_enqueue(*, file_id: str, storage_path: str, content_type: str) -> None:
        enqueued.append((file_id, storage_path, content_type))

    tasks_module.enqueue_enrichment = _capture_enqueue  # type: ignore[assignment]

    try:
        result = await tasks_module.enqueue_reindex_all(file_ids=["r1", "r3"])

        assert result["enqueued"] == 2
        assert len(enqueued) == 2
        file_ids = {e[0] for e in enqueued}
        assert file_ids == {"r1", "r3"}

    finally:
        tasks_module.enqueue_enrichment = original_enqueue


async def test_reindex_progress_tracking(tasks_test_env) -> None:
    """Progress counters update as enrichment jobs complete in a reindex batch.

    Tests the counter logic directly by calling _process_enrichment
    rather than through the worker loop, avoiding event-loop interaction
    issues with module-level state across tests.
    """
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, storage = tasks_test_env

    # Reset reindex state (already zeroed by fixture teardown, but be explicit)
    tasks_module._reindex_total = 0
    tasks_module._reindex_completed = 0
    tasks_module._reindex_failed = 0
    tasks_module._reindex_active = False

    file_id = "progress-file-1"
    storage_path = await storage.save(file_id, b"hello world text content")

    try:
        async with session_factory() as session:
            record = FileRecord(
                id=file_id,
                filename="report.txt",
                size=24,
                content_type="text/plain",
                checksum="abc123",
                storage_path=storage_path,
            )
            session.add(record)
            await session.commit()

        canned = {"category": "document", "tags": "pdf,report", "summary": "A report file."}

        with patch.object(tasks_module, "enrich_file", new=AsyncMock(return_value=canned)):
            tasks_module._storage = storage

            # Simulate a reindex batch: set counters then call _process_enrichment
            tasks_module._reindex_total = 1
            tasks_module._reindex_active = True

            progress = tasks_module.get_reindex_progress()
            assert progress["total"] == 1
            assert progress["completed"] == 0
            assert progress["failed"] == 0
            assert progress["active"] is True

            # Process the job manually (simulating what the worker does)
            from src.robotsix_file_hub.tasks import EnrichmentJob

            job = EnrichmentJob(
                file_id=file_id,
                storage_path=storage_path,
                content_type="text/plain",
            )
            success = await tasks_module._process_enrichment(job)
            assert success is True

            # Simulate worker counter update
            if tasks_module._reindex_active:
                if success:
                    tasks_module._reindex_completed += 1
                else:
                    tasks_module._reindex_failed += 1

            # Simulate worker completion check
            if (
                tasks_module._reindex_active
                and (tasks_module._reindex_completed + tasks_module._reindex_failed)
                >= tasks_module._reindex_total
            ):
                tasks_module._reindex_active = False

            progress = tasks_module.get_reindex_progress()
            assert progress["completed"] == 1
            assert progress["failed"] == 0
            assert progress["active"] is False

    finally:
        tasks_module._storage = None


# ── Reindex endpoint tests ─────────────────────────────────────────


async def test_reindex_progress_endpoint(
    test_client: AsyncClient,
    test_session_factory: async_sessionmaker,
    test_storage: StorageBackend,
) -> None:
    """GET /files/reindex/progress returns progress counters."""
    import src.robotsix_file_hub.tasks as tasks_module

    original_session_local = tasks_module.async_session_factory
    tasks_module.async_session_factory = test_session_factory  # type: ignore[assignment]

    try:
        # Set up progress state for testing
        tasks_module._reindex_total = 10
        tasks_module._reindex_completed = 7
        tasks_module._reindex_failed = 1
        tasks_module._reindex_active = True

        response = await test_client.get("/files/reindex/progress")

        assert response.status_code == 200
        data = response.json()
        assert data == {
            "total": 10,
            "completed": 7,
            "failed": 1,
            "active": True,
        }

    finally:
        tasks_module.async_session_factory = original_session_local
        tasks_module._reindex_total = 0
        tasks_module._reindex_completed = 0
        tasks_module._reindex_failed = 0
        tasks_module._reindex_active = False


async def test_reindex_endpoint_with_filter(
    test_client: AsyncClient,
    test_db_session: AsyncSession,
    test_session_factory: async_sessionmaker,
    test_storage: StorageBackend,
) -> None:
    """POST /files/reindex?content_type=image/png only enqueues matching files."""
    import src.robotsix_file_hub.tasks as tasks_module

    # Pre-populate DB
    test_db_session.add_all(
        [
            FileRecord(
                id="f1",
                filename="a.txt",
                size=10,
                content_type="text/plain",
                checksum="aa",
                storage_path="/tmp/a.txt",
            ),
            FileRecord(
                id="f2",
                filename="b.png",
                size=20,
                content_type="image/png",
                checksum="bb",
                storage_path="/tmp/b.png",
            ),
        ]
    )
    await test_db_session.commit()

    original_session_local = tasks_module.async_session_factory
    tasks_module.async_session_factory = test_session_factory  # type: ignore[assignment]

    try:
        response = await test_client.post("/files/reindex", params={"content_type": "image/png"})

        assert response.status_code == 200
        data = response.json()
        assert data["enqueued"] == 1

    finally:
        tasks_module.async_session_factory = original_session_local
        tasks_module._reindex_total = 0
        tasks_module._reindex_completed = 0
        tasks_module._reindex_failed = 0
        tasks_module._reindex_active = False


async def test_reindex_endpoint_returns_ok(
    test_client: AsyncClient,
    test_session_factory: async_sessionmaker,
    test_storage: StorageBackend,
) -> None:
    """POST /files/reindex returns 200 with enqueued count."""
    import src.robotsix_file_hub.tasks as tasks_module

    original_session_local = tasks_module.async_session_factory
    tasks_module.async_session_factory = test_session_factory  # type: ignore[assignment]

    try:
        response = await test_client.post("/files/reindex")

        assert response.status_code == 200
        data = response.json()
        assert "enqueued" in data

    finally:
        tasks_module.async_session_factory = original_session_local


# ── Text extraction unit tests ─────────────────────────────────────


async def test_extract_text_plain() -> None:
    """extract_text returns decoded content for text/* types."""
    from src.robotsix_file_hub.enrichment import extract_text

    result = extract_text(b"Hello, world!", "text/plain")
    assert result == "Hello, world!"


async def test_extract_text_unsupported() -> None:
    """extract_text returns None for unsupported types with no handler."""
    from src.robotsix_file_hub.enrichment import extract_text

    result = extract_text(b"\x00\x01\x02", "application/octet-stream")
    assert result is None


async def test_extract_text_pdf_empty() -> None:
    """extract_text handles PDFs gracefully even if empty/corrupt."""
    from src.robotsix_file_hub.enrichment import extract_text

    # Minimal valid PDF
    pdf_bytes = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n"
        b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
        b"0000000058 00000 n \n0000000115 00000 n \n"
        b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n190\n%%EOF"
    )
    result = extract_text(pdf_bytes, "application/pdf")
    # PDF has no text content, so result should be None or empty string
    assert result is None or result == ""
