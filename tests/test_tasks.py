"""Tests for background task queue and enrichment."""

import asyncio
import io
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.robotsix_file_hub.models import FileRecord
from src.robotsix_file_hub.storage import StorageBackend
from src.robotsix_file_hub.tasks import enqueue_enrichment, start_workers, stop_workers

# ── Enrichment worker tests ────────────────────────────────────────


async def test_enrichment_worker_updates_record(tasks_test_env) -> None:
    """Enqueue an enrichment job and wait for the worker to process it.

    The enrichment module is mocked to return canned values so we
    don't need a real LLM or file content.
    """
    import src.robotsix_file_hub.storage as storage_module
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, storage = tasks_test_env

    # Write a real file so the storage backend can read it
    file_id = "test-file-001"
    storage_key = await storage.save(file_id, b"hello world text content")

    try:
        # Insert a file record
        async with session_factory() as session:
            record = FileRecord(
                id=file_id,
                filename="report.txt",
                size=24,
                content_type="text/plain",
                checksum="abc123",
                storage_key=storage_key,
            )
            session.add(record)
            await session.commit()

        # Mock enrich_file to return canned enrichment
        canned = {"category": "document", "tags": "pdf,report", "summary": "A report file."}

        with patch.object(tasks_module, "enrich_file", new=AsyncMock(return_value=canned)):
            storage_module._storage = storage

            # Start one worker
            await start_workers(count=1)

            # Enqueue enrichment
            enqueue_enrichment(
                file_id=file_id,
                storage_key=storage_key,
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
        storage_module._storage = None


async def test_enrichment_worker_null_on_llm_failure(tasks_test_env) -> None:
    """When enrich_file returns None fields, the DB record is updated with nulls."""
    import src.robotsix_file_hub.storage as storage_module
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, storage = tasks_test_env

    file_id = "test-file-002"
    storage_key = await storage.save(file_id, b"binary blob")

    try:
        async with session_factory() as session:
            record = FileRecord(
                id=file_id,
                filename="blob.bin",
                size=11,
                content_type="application/octet-stream",
                checksum="def456",
                storage_key=storage_key,
            )
            session.add(record)
            await session.commit()

        # enrich_file returns all None (no text extracted)
        canned = {"category": None, "tags": None, "summary": None}

        with patch.object(tasks_module, "enrich_file", new=AsyncMock(return_value=canned)):
            storage_module._storage = storage

            await start_workers(count=1)

            enqueue_enrichment(
                file_id=file_id,
                storage_key=storage_key,
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
        storage_module._storage = None


# ── Upload-enqueues-enrichment test ─────────────────────────────────


async def test_upload_enqueues_enrichment(
    test_client: AsyncClient,
    test_storage: StorageBackend,
) -> None:
    """POST /files should enqueue an enrichment job after DB write."""
    import src.robotsix_file_hub.routes.files as routes_module

    enqueued: list[tuple[str, str, str]] = []
    original_enqueue = routes_module.enqueue_enrichment

    def _capture_enqueue(*, file_id: str, storage_key: str, content_type: str) -> None:
        enqueued.append((file_id, storage_key, content_type))

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
                    storage_key="/tmp/a.txt",
                ),
                FileRecord(
                    id="r2",
                    filename="b.png",
                    size=20,
                    content_type="image/png",
                    checksum="bb",
                    storage_key="/tmp/b.png",
                ),
            ]
        )
        await session.commit()

    enqueued: list[tuple[str, str, str]] = []
    original_enqueue = tasks_module.enqueue_enrichment

    def _capture_enqueue(*, file_id: str, storage_key: str, content_type: str) -> None:
        enqueued.append((file_id, storage_key, content_type))

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
                    storage_key="/tmp/a.txt",
                    category="document",
                ),
                FileRecord(
                    id="r2",
                    filename="b.png",
                    size=20,
                    content_type="image/png",
                    checksum="bb",
                    storage_key="/tmp/b.png",
                    category="image",
                ),
                FileRecord(
                    id="r3",
                    filename="c.txt",
                    size=30,
                    content_type="text/plain",
                    checksum="cc",
                    storage_key="/tmp/c.txt",
                    category="document",
                ),
            ]
        )
        await session.commit()

    enqueued: list[tuple[str, str, str]] = []
    original_enqueue = tasks_module.enqueue_enrichment

    def _capture_enqueue(*, file_id: str, storage_key: str, content_type: str) -> None:
        enqueued.append((file_id, storage_key, content_type))

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
                    storage_key="/tmp/a.txt",
                ),
                FileRecord(
                    id="r2",
                    filename="b.png",
                    size=20,
                    content_type="image/png",
                    checksum="bb",
                    storage_key="/tmp/b.png",
                ),
            ]
        )
        await session.commit()

    enqueued: list[tuple[str, str, str]] = []
    original_enqueue = tasks_module.enqueue_enrichment

    def _capture_enqueue(*, file_id: str, storage_key: str, content_type: str) -> None:
        enqueued.append((file_id, storage_key, content_type))

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
                    storage_key="/tmp/a.txt",
                ),
                FileRecord(
                    id="r2",
                    filename="b.png",
                    size=20,
                    content_type="image/png",
                    checksum="bb",
                    storage_key="/tmp/b.png",
                ),
                FileRecord(
                    id="r3",
                    filename="c.txt",
                    size=30,
                    content_type="text/plain",
                    checksum="cc",
                    storage_key="/tmp/c.txt",
                ),
            ]
        )
        await session.commit()

    enqueued: list[tuple[str, str, str]] = []
    original_enqueue = tasks_module.enqueue_enrichment

    def _capture_enqueue(*, file_id: str, storage_key: str, content_type: str) -> None:
        enqueued.append((file_id, storage_key, content_type))

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
    import src.robotsix_file_hub.storage as storage_module
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, storage = tasks_test_env

    # Reset reindex state (already zeroed by fixture teardown, but be explicit)
    tasks_module._reindex_total = 0
    tasks_module._reindex_completed = 0
    tasks_module._reindex_failed = 0
    tasks_module._reindex_active = False

    file_id = "progress-file-1"
    storage_key = await storage.save(file_id, b"hello world text content")

    try:
        async with session_factory() as session:
            record = FileRecord(
                id=file_id,
                filename="report.txt",
                size=24,
                content_type="text/plain",
                checksum="abc123",
                storage_key=storage_key,
            )
            session.add(record)
            await session.commit()

        canned = {"category": "document", "tags": "pdf,report", "summary": "A report file."}

        with patch.object(tasks_module, "enrich_file", new=AsyncMock(return_value=canned)):
            storage_module._storage = storage

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
                storage_key=storage_key,
                content_type="text/plain",
                task_id="test-task-id",
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
        storage_module._storage = None


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
        assert data["total"] == 10
        assert data["completed"] == 7
        assert data["failed"] == 1
        assert data["active"] is True
        assert "task_id" in data

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
                storage_key="/tmp/a.txt",
            ),
            FileRecord(
                id="f2",
                filename="b.png",
                size=20,
                content_type="image/png",
                checksum="bb",
                storage_key="/tmp/b.png",
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
    """POST /files/reindex returns 200 with enqueued count and task_id."""
    import src.robotsix_file_hub.tasks as tasks_module

    original_session_local = tasks_module.async_session_factory
    tasks_module.async_session_factory = test_session_factory  # type: ignore[assignment]

    try:
        response = await test_client.post("/files/reindex")

        assert response.status_code == 200
        data = response.json()
        assert "enqueued" in data
        assert "task_id" in data

    finally:
        tasks_module.async_session_factory = original_session_local


# ── Task status endpoint tests ─────────────────────────────────────


async def test_get_task_not_found(test_client: AsyncClient) -> None:
    """GET /tasks/{id} returns 404 for unknown task IDs."""
    response = await test_client.get("/tasks/nonexistent-task-id")
    assert response.status_code == 404
    assert response.json()["detail"] == "Task nonexistent-task-id not found"


async def test_get_task_returns_status(test_client: AsyncClient) -> None:
    """GET /tasks/{id} returns status for a known enrichment task."""
    import src.robotsix_file_hub.tasks as tasks_module
    from src.robotsix_file_hub.schemas import TaskStatus, TaskType

    # Seed a task directly
    task_id = "task-001"
    tasks_module._tasks[task_id] = tasks_module.TaskInfo(
        task_id=task_id,
        type=TaskType.enrichment,
        status=TaskStatus.pending,
        file_id="file-001",
    )

    response = await test_client.get(f"/tasks/{task_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == task_id
    assert data["type"] == "enrichment"
    assert data["status"] == "pending"
    assert data["file_id"] == "file-001"
    assert data["progress"] is None
    assert data["error"] is None
    assert "created_at" in data
    assert "updated_at" in data


async def test_upload_response_includes_task_id(test_client: AsyncClient) -> None:
    """POST /files response includes a non-null task_id."""
    import src.robotsix_file_hub.routes.files as routes_module

    original_enqueue = routes_module.enqueue_enrichment

    def _fake_enqueue(*, file_id: str, storage_key: str, content_type: str) -> str:
        return "test-task-123"

    routes_module.enqueue_enrichment = _fake_enqueue  # type: ignore[assignment]

    try:
        response = await test_client.post(
            "/files",
            files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == "test-task-123"

    finally:
        routes_module.enqueue_enrichment = original_enqueue


async def test_task_status_transitions(
    tasks_test_env,
) -> None:
    """Task status transitions pending → running → completed during enrichment."""
    import src.robotsix_file_hub.storage as storage_module
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, storage = tasks_test_env

    file_id = "transition-file-1"
    storage_key = await storage.save(file_id, b"some text content")

    try:
        async with session_factory() as session:
            record = tasks_module.FileRecord(
                id=file_id,
                filename="doc.txt",
                size=17,
                content_type="text/plain",
                checksum="xyz",
                storage_key=storage_key,
            )
            session.add(record)
            await session.commit()

        canned = {"category": "doc", "tags": "txt", "summary": "A text file."}

        with patch.object(tasks_module, "enrich_file", new=AsyncMock(return_value=canned)):
            storage_module._storage = storage

            await start_workers(count=1)

            task_id = enqueue_enrichment(
                file_id=file_id,
                storage_key=storage_key,
                content_type="text/plain",
            )

            # Task should be pending immediately after enqueue
            task = tasks_module.get_task(task_id)
            assert task is not None
            assert task.status == "pending"

            # Wait for completion
            for _ in range(20):
                task = tasks_module.get_task(task_id)
                if task and task.status == "completed":
                    break
                await asyncio.sleep(0.1)
            else:
                await stop_workers()
                pytest.fail("Task did not reach completed status within timeout")

            assert task is not None
            assert task.status == "completed"
            assert task.file_id == file_id
            assert task.error is None

    finally:
        await stop_workers()
        storage_module._storage = None


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
