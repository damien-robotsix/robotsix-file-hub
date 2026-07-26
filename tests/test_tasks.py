"""Tests for background task queue and enrichment."""

import asyncio
import io
import os
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.robotsix_file_hub.database import get_db
from src.robotsix_file_hub.main import app
from src.robotsix_file_hub.models import Base, FileRecord
from src.robotsix_file_hub.storage import LocalStorageBackend
from src.robotsix_file_hub.tasks import enqueue_enrichment, start_workers, stop_workers


@pytest.fixture
def tmp_upload_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


async def test_enrichment_worker_updates_record() -> None:
    """Enqueue an enrichment job and wait for the worker to process it."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        database_url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_async_engine(database_url, echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

        # Monkey-patch async_session_factory for the worker
        import src.robotsix_file_hub.tasks as tasks_module

        original_session_local = tasks_module.async_session_factory
        tasks_module.async_session_factory = session_factory  # type: ignore[assignment]

        try:
            # Insert a file record
            file_id = "test-file-001"
            async with session_factory() as session:
                record = FileRecord(
                    id=file_id,
                    filename="report.pdf",
                    size=1024,
                    content_type="application/pdf",
                    checksum="abc123",
                    storage_path="/tmp/report.pdf",
                )
                session.add(record)
                await session.commit()

            # Start one worker
            await start_workers(count=1)

            # Enqueue enrichment
            enqueue_enrichment(
                file_id=file_id,
                filename="report.pdf",
                content_type="application/pdf",
            )

            # Wait for the job to be processed (poll)
            for _ in range(20):
                async with session_factory() as session:
                    record = await session.get(FileRecord, file_id)
                    if record and record.category is not None:
                        break
                await asyncio.sleep(0.1)
            else:
                await stop_workers()
                tasks_module.async_session_factory = original_session_local
                await engine.dispose()
                pytest.fail("Enrichment was not processed within timeout")

            # Verify enrichment fields were populated
            async with session_factory() as session:
                record = await session.get(FileRecord, file_id)
                assert record is not None
                assert record.category == "document"
                assert record.tags is not None
                assert "pdf" in record.tags
                assert record.summary is not None
                assert record.source == "upload"

        finally:
            await stop_workers()
            tasks_module.async_session_factory = original_session_local
            await engine.dispose()


async def test_upload_enqueues_enrichment(tmp_upload_dir: str) -> None:
    """POST /files should enqueue an enrichment job after DB write."""
    import src.robotsix_file_hub.routes.files as routes_module

    storage = LocalStorageBackend(base_path=tmp_upload_dir)
    routes_module._storage = storage

    db_path = os.path.join(tmp_upload_dir, "test.db")
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db() -> AsyncSession:  # type: ignore[misc]
        async with session_factory() as session:
            yield session

    original_deps = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db

    # Patch the route module's reference (import-time binding)
    enqueued: list[tuple[str, str, str]] = []
    original_enqueue = routes_module.enqueue_enrichment

    def _capture_enqueue(*, file_id: str, filename: str, content_type: str) -> None:
        enqueued.append((file_id, filename, content_type))

    routes_module.enqueue_enrichment = _capture_enqueue  # type: ignore[assignment]

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/files",
                files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
            )

        assert response.status_code == 200
        data = response.json()
        assert len(enqueued) == 1
        assert enqueued[0][0] == data["id"]
        assert enqueued[0][1] == "notes.txt"
        assert enqueued[0][2] == "text/plain"

    finally:
        app.dependency_overrides = original_deps
        routes_module._storage = None
        routes_module.enqueue_enrichment = original_enqueue
        await engine.dispose()


async def test_reindex_all_enqueues_all_files() -> None:
    """enqueue_reindex_all enqueues enrichment for every file in the DB."""
    import src.robotsix_file_hub.tasks as tasks_module

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        database_url = f"sqlite+aiosqlite:///{db_path}"
        engine = create_async_engine(database_url, echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)

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

        original_session_local = tasks_module.async_session_factory
        tasks_module.async_session_factory = session_factory  # type: ignore[assignment]

        enqueued: list[tuple[str, str, str]] = []
        original_enqueue = tasks_module.enqueue_enrichment

        def _capture_enqueue(*, file_id: str, filename: str, content_type: str) -> None:
            enqueued.append((file_id, filename, content_type))

        tasks_module.enqueue_enrichment = _capture_enqueue  # type: ignore[assignment]

        try:
            result = await tasks_module.enqueue_reindex_all()

            assert result["enqueued"] == 2
            assert len(enqueued) == 2
            file_ids = {e[0] for e in enqueued}
            assert file_ids == {"r1", "r2"}

        finally:
            tasks_module.async_session_factory = original_session_local
            tasks_module.enqueue_enrichment = original_enqueue
            await engine.dispose()


async def test_reindex_endpoint_returns_ok(tmp_upload_dir: str) -> None:
    """POST /files/reindex returns 200 with enqueued count."""
    import src.robotsix_file_hub.routes.files as routes_module
    import src.robotsix_file_hub.tasks as tasks_module

    storage = LocalStorageBackend(base_path=tmp_upload_dir)
    routes_module._storage = storage

    db_path = os.path.join(tmp_upload_dir, "test.db")
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db() -> AsyncSession:  # type: ignore[misc]
        async with session_factory() as session:
            yield session

    original_deps = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db

    # Patch the route module's reference so the endpoint uses test DB
    original_session_local = tasks_module.async_session_factory
    tasks_module.async_session_factory = session_factory  # type: ignore[assignment]

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/files/reindex")

        assert response.status_code == 200
        data = response.json()
        assert "enqueued" in data

    finally:
        app.dependency_overrides = original_deps
        routes_module._storage = None
        tasks_module.async_session_factory = original_session_local
        await engine.dispose()


async def test_category_derivation() -> None:
    """Verify _derive_category returns expected values."""
    from src.robotsix_file_hub.tasks import _derive_category

    assert _derive_category("image/png", "photo.png") == "image"
    assert _derive_category("video/mp4", "clip.mp4") == "video"
    assert _derive_category("audio/mpeg", "song.mp3") == "audio"
    assert _derive_category("text/plain", "readme.txt") == "document"
    assert _derive_category("application/pdf", "report.pdf") == "document"
    assert (
        _derive_category(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "data.xlsx"
        )
        == "spreadsheet"
    )
    assert _derive_category("application/octet-stream", "unknown.xyz") == "other"


async def test_tags_derivation() -> None:
    """Verify _derive_tags extracts extension and name chunks."""
    from src.robotsix_file_hub.tasks import _derive_tags

    tags = _derive_tags("quarterly_report_2024.pdf").split(",")
    assert "pdf" in tags
    assert "quarterly" in tags
    assert "report" in tags
    assert "2024" in tags
