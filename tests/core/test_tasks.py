"""Tests for the background task queue and enrichment worker."""

import asyncio
import io
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

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

        # Embedding is mocked too: it is an outbound HTTP call to the
        # configured endpoint, and these tests assert on the enrichment
        # worker, not on a live embedding backend.
        with (
            patch.object(tasks_module, "enrich_file", new=AsyncMock(return_value=canned)),
            patch.object(
                tasks_module, "generate_embedding", new=AsyncMock(return_value=[0.1, 0.2])
            ),
        ):
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

        # Embedding is mocked too: it is an outbound HTTP call to the
        # configured endpoint, and these tests assert on the enrichment
        # worker, not on a live embedding backend.
        with (
            patch.object(tasks_module, "enrich_file", new=AsyncMock(return_value=canned)),
            patch.object(
                tasks_module, "generate_embedding", new=AsyncMock(return_value=[0.1, 0.2])
            ),
        ):
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
    import src.robotsix_file_hub.routes.upload as routes_module

    enqueued: list[tuple[str, str, str]] = []
    original_enqueue = routes_module.enqueue_enrichment

    def _capture_enqueue(
        *,
        file_id: str,
        storage_key: str,
        content_type: str,
        force: bool = False,
        upload_metadata: str | None = None,
    ) -> None:
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


# ── Worker loop unit tests ─────────────────────────────────────────


async def test_worker_handles_enrichment_exception(tasks_test_env) -> None:
    """When _process_enrichment raises, the worker marks the task failed.

    Exercises the ``except Exception`` branch inside ``_worker`` that
    sets ``error="Worker exception"`` and increments the reindex
    failed counter.
    """
    import src.robotsix_file_hub.storage as storage_module
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, storage = tasks_test_env

    file_id = "exception-worker-1"
    storage_key = await storage.save(file_id, b"test content")

    try:
        async with session_factory() as session:
            record = tasks_module.FileRecord(
                id=file_id,
                filename="test.txt",
                size=12,
                content_type="text/plain",
                checksum="abc",
                storage_key=storage_key,
            )
            session.add(record)
            await session.commit()

        storage_module._storage = storage

        # Patch _process_enrichment to raise an exception
        with patch.object(
            tasks_module, "_process_enrichment", new=AsyncMock(side_effect=RuntimeError("boom"))
        ):
            await tasks_module.start_workers(count=1)

            task_id = tasks_module.enqueue_enrichment(
                file_id=file_id,
                storage_key=storage_key,
                content_type="text/plain",
            )

            # Wait for task to reach failed status
            for _ in range(20):
                task = tasks_module.get_task(task_id)
                if task and task.status == "failed":
                    break
                await asyncio.sleep(0.1)
            else:
                await tasks_module.stop_workers()
                pytest.fail("Task did not reach failed status within timeout")

            task = tasks_module.get_task(task_id)
            assert task is not None
            assert task.status == "failed"
            assert task.error == "Worker exception"

    finally:
        await tasks_module.stop_workers()
        storage_module._storage = None


async def test_worker_reindex_completion_detection(tasks_test_env) -> None:
    """The worker's finally block flips _reindex_active and completes the reindex task.

    Exercises the completion-detection logic inside ``_worker``'s
    ``finally`` block: when all jobs in a reindex batch finish,
    ``_reindex_active`` becomes False and the parent reindex task is
    marked ``completed``.
    """
    import src.robotsix_file_hub.storage as storage_module
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, storage = tasks_test_env

    file_id = "reindex-worker-1"
    storage_key = await storage.save(file_id, b"test content")

    try:
        async with session_factory() as session:
            record = tasks_module.FileRecord(
                id=file_id,
                filename="test.txt",
                size=12,
                content_type="text/plain",
                checksum="abc",
                storage_key=storage_key,
            )
            session.add(record)
            await session.commit()

        canned = {"category": "doc", "tags": "test", "summary": "A test file."}

        # Embedding is mocked too: it is an outbound HTTP call to the
        # configured endpoint, and these tests assert on the enrichment
        # worker, not on a live embedding backend.
        with (
            patch.object(tasks_module, "enrich_file", new=AsyncMock(return_value=canned)),
            patch.object(
                tasks_module, "generate_embedding", new=AsyncMock(return_value=[0.1, 0.2])
            ),
        ):
            storage_module._storage = storage

            # Set up reindex state so the worker's finally block runs
            # completion detection
            tasks_module._reindex_total = 1
            tasks_module._reindex_completed = 0
            tasks_module._reindex_failed = 0
            tasks_module._reindex_active = True
            tasks_module._reindex_task_id = "reindex-task-test"
            tasks_module._tasks["reindex-task-test"] = tasks_module.TaskInfo(
                task_id="reindex-task-test",
                type=tasks_module.TaskType.reindex,
                status=tasks_module.TaskStatus.running,
            )

            await tasks_module.start_workers(count=1)

            tasks_module.enqueue_enrichment(
                file_id=file_id,
                storage_key=storage_key,
                content_type="text/plain",
            )

            # Wait for the worker to complete the batch and flip _reindex_active
            for _ in range(30):
                if not tasks_module._reindex_active:
                    break
                await asyncio.sleep(0.1)
            else:
                await tasks_module.stop_workers()
                pytest.fail("Worker did not complete reindex within timeout")

            assert tasks_module._reindex_active is False
            assert tasks_module._reindex_completed == 1
            assert tasks_module._reindex_failed == 0

            reindex_task = tasks_module.get_task("reindex-task-test")
            assert reindex_task is not None
            assert reindex_task.status == "completed"

    finally:
        await tasks_module.stop_workers()
        storage_module._storage = None


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
    import src.robotsix_file_hub.routes.upload as routes_module

    original_enqueue = routes_module.enqueue_enrichment

    def _fake_enqueue(
        *,
        file_id: str,
        storage_key: str,
        content_type: str,
        upload_metadata: str | None = None,
    ) -> str:
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

        # Embedding is mocked too: it is an outbound HTTP call to the
        # configured endpoint, and these tests assert on the enrichment
        # worker, not on a live embedding backend.
        with (
            patch.object(tasks_module, "enrich_file", new=AsyncMock(return_value=canned)),
            patch.object(
                tasks_module, "generate_embedding", new=AsyncMock(return_value=[0.1, 0.2])
            ),
        ):
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
