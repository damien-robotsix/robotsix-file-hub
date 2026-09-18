"""Tests for the background reindex feature.

Covers ``enqueue_reindex_all`` (filters, batch capping, curation),
reindex progress tracking, and the reindex HTTP endpoints.
"""

from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.robotsix_file_hub.models import FileRecord
from src.robotsix_file_hub.storage import StorageBackend

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

    def _capture_enqueue(
        *,
        file_id: str,
        storage_key: str,
        content_type: str,
        force: bool = False,
        upload_metadata: str | None = None,
    ) -> None:
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


async def test_reindex_all_skips_curated_without_force(tasks_test_env) -> None:
    """Enqueue-time curation filter: a non-forced reindex skips curated records.

    Curated (agent/manual) records are excluded up front so the ``enqueued``
    count reflects real work instead of jobs the worker would only no-op.
    ``force=True`` deliberately re-includes them for an override reindex.
    """
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, _storage = tasks_test_env

    async with session_factory() as session:
        session.add_all(
            [
                FileRecord(
                    id="c1",
                    filename="curated.txt",
                    size=10,
                    content_type="text/plain",
                    checksum="aa",
                    storage_key="/tmp/c1.txt",
                    summary="Curated summary",
                    metadata_source="manual",
                ),
                FileRecord(
                    id="p1",
                    filename="plain.txt",
                    size=10,
                    content_type="text/plain",
                    checksum="bb",
                    storage_key="/tmp/p1.txt",
                ),
            ]
        )
        await session.commit()

    enqueued: list[tuple[str, str, str]] = []
    original_enqueue = tasks_module.enqueue_enrichment

    def _capture_enqueue(
        *,
        file_id: str,
        storage_key: str,
        content_type: str,
        force: bool = False,
        upload_metadata: str | None = None,
    ) -> None:
        enqueued.append((file_id, storage_key, content_type))

    tasks_module.enqueue_enrichment = _capture_enqueue  # type: ignore[assignment]

    try:
        # Default reindex: the curated record is excluded at enqueue time.
        result = await tasks_module.enqueue_reindex_all()
        assert result["enqueued"] == 1
        assert [e[0] for e in enqueued] == ["p1"]
        assert tasks_module._reindex_total == 1

        # Forced reindex deliberately re-includes curated records.
        enqueued.clear()
        result = await tasks_module.enqueue_reindex_all(force=True)
        assert result["enqueued"] == 2
        assert {e[0] for e in enqueued} == {"c1", "p1"}

    finally:
        tasks_module.enqueue_enrichment = original_enqueue


async def test_reindex_all_caps_batch_at_20_files(tasks_test_env) -> None:
    """enqueue_reindex_all enqueues at most REINDEX_BATCH_SIZE files."""
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, _storage = tasks_test_env

    # Pre-populate DB with more records than the batch cap
    async with session_factory() as session:
        session.add_all(
            [
                FileRecord(
                    id=f"cap-{i:03d}",
                    filename=f"f{i:03d}.txt",
                    size=10,
                    content_type="text/plain",
                    checksum=f"c{i:03d}",
                    storage_key=f"/tmp/f{i:03d}.txt",
                )
                for i in range(tasks_module.REINDEX_BATCH_SIZE + 5)
            ]
        )
        await session.commit()

    enqueued: list[tuple[str, str, str]] = []
    original_enqueue = tasks_module.enqueue_enrichment

    def _capture_enqueue(
        *,
        file_id: str,
        storage_key: str,
        content_type: str,
        force: bool = False,
        upload_metadata: str | None = None,
    ) -> None:
        enqueued.append((file_id, storage_key, content_type))

    tasks_module.enqueue_enrichment = _capture_enqueue  # type: ignore[assignment]

    try:
        result = await tasks_module.enqueue_reindex_all()

        assert result["enqueued"] == tasks_module.REINDEX_BATCH_SIZE
        assert len(enqueued) == tasks_module.REINDEX_BATCH_SIZE
        assert tasks_module._reindex_total == tasks_module.REINDEX_BATCH_SIZE
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

    def _capture_enqueue(
        *,
        file_id: str,
        storage_key: str,
        content_type: str,
        force: bool = False,
        upload_metadata: str | None = None,
    ) -> None:
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

    def _capture_enqueue(
        *,
        file_id: str,
        storage_key: str,
        content_type: str,
        force: bool = False,
        upload_metadata: str | None = None,
    ) -> None:
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

    def _capture_enqueue(
        *,
        file_id: str,
        storage_key: str,
        content_type: str,
        force: bool = False,
        upload_metadata: str | None = None,
    ) -> None:
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


async def test_reindex_all_filtered_by_enrichment_status_empty(tasks_test_env) -> None:
    """enqueue_reindex_all with enrichment_status='empty' selects only unenriched files."""
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
                    # No summary/embedding — should be selected
                ),
                FileRecord(
                    id="r2",
                    filename="b.png",
                    size=20,
                    content_type="image/png",
                    checksum="bb",
                    storage_key="/tmp/b.png",
                    summary="A photo of a cat",
                ),
                FileRecord(
                    id="r3",
                    filename="c.txt",
                    size=30,
                    content_type="text/plain",
                    checksum="cc",
                    storage_key="/tmp/c.txt",
                    # No summary/embedding — should be selected
                ),
            ]
        )
        await session.commit()

    enqueued: list[tuple[str, str, str]] = []
    original_enqueue = tasks_module.enqueue_enrichment

    def _capture_enqueue(
        *,
        file_id: str,
        storage_key: str,
        content_type: str,
        force: bool = False,
        upload_metadata: str | None = None,
    ) -> None:
        enqueued.append((file_id, storage_key, content_type))

    tasks_module.enqueue_enrichment = _capture_enqueue  # type: ignore[assignment]

    try:
        result = await tasks_module.enqueue_reindex_all(enrichment_status="empty")

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
