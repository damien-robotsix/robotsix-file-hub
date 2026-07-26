"""Tests for embedding generation and storage."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.robotsix_file_hub.models import Base, FileRecord
from src.robotsix_file_hub.storage import LocalStorageBackend


@pytest.fixture
async def embedding_test_env(tmp_upload_dir: str):
    """Set up engine, session factory, storage, and monkey-patch tasks_module.

    Yields ``(session_factory, storage)`` for embedding integration tests
    that call ``_process_enrichment`` directly.
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
        await engine.dispose()


# ── Unit tests ─────────────────────────────────────────────────────


async def test_build_embedding_text_all_fields() -> None:
    """build_embedding_text concatenates all non-null fields."""
    from src.robotsix_file_hub.embeddings import build_embedding_text

    result = build_embedding_text(
        filename="report.pdf",
        summary="A quarterly report",
        tags="finance,quarterly",
        category="document",
    )
    assert "report.pdf" in result
    assert "A quarterly report" in result
    assert "finance,quarterly" in result
    assert "document" in result


async def test_build_embedding_text_null_fields() -> None:
    """build_embedding_text skips None fields."""
    from src.robotsix_file_hub.embeddings import build_embedding_text

    result = build_embedding_text(
        filename="notes.txt",
        summary=None,
        tags=None,
        category=None,
    )
    assert result == "notes.txt"


async def test_generate_embedding_returns_floats() -> None:
    """generate_embedding returns a list of floats with correct dimensionality."""
    import src.robotsix_file_hub.embeddings as emb_module
    from src.robotsix_file_hub.embeddings import generate_embedding

    original_model = emb_module._model

    mock_model = MagicMock()
    mock_embedding = np.random.rand(384).astype(np.float32)
    mock_model.encode.return_value = mock_embedding
    emb_module._model = mock_model

    try:
        result = generate_embedding("test text")
        assert isinstance(result, list)
        assert len(result) == 384
        assert all(isinstance(v, float) for v in result)
        mock_model.encode.assert_called_once_with("test text", normalize_embeddings=True)
    finally:
        emb_module._model = original_model


# ── Integration tests ──────────────────────────────────────────────


async def test_embedding_stored_during_enrichment(embedding_test_env) -> None:
    """_process_enrichment stores the embedding on the DB record."""
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, storage = embedding_test_env

    file_id = "embed-test-001"
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

        canned_enrichment = {
            "category": "document",
            "tags": "pdf,report",
            "summary": "A report file.",
        }
        fake_embedding = [0.1, 0.2, 0.3]

        with (
            patch.object(
                tasks_module, "enrich_file", new=AsyncMock(return_value=canned_enrichment)
            ),
            patch.object(tasks_module, "generate_embedding", return_value=fake_embedding),
        ):
            tasks_module._storage = storage

            from src.robotsix_file_hub.tasks import EnrichmentJob

            job = EnrichmentJob(
                file_id=file_id,
                storage_key=storage_key,
                content_type="text/plain",
            )
            success = await tasks_module._process_enrichment(job)
            assert success is True

        # Verify the embedding was stored
        async with session_factory() as session:
            r = await session.get(FileRecord, file_id)
            assert r is not None
            assert r.embedding == fake_embedding
            assert r.category == "document"

    finally:
        tasks_module._storage = None


async def test_embedding_null_on_generation_failure(embedding_test_env) -> None:
    """When embedding generation raises, embedding is set to None (best-effort)."""
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, storage = embedding_test_env

    file_id = "embed-fail-001"
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

        canned_enrichment = {
            "category": "other",
            "tags": "binary",
            "summary": "Unknown content.",
        }

        with (
            patch.object(
                tasks_module, "enrich_file", new=AsyncMock(return_value=canned_enrichment)
            ),
            patch.object(
                tasks_module,
                "generate_embedding",
                side_effect=RuntimeError("model not loaded"),
            ),
        ):
            tasks_module._storage = storage

            from src.robotsix_file_hub.tasks import EnrichmentJob

            job = EnrichmentJob(
                file_id=file_id,
                storage_key=storage_key,
                content_type="application/octet-stream",
            )
            success = await tasks_module._process_enrichment(job)
            assert success is True

        # Enrichment fields populated, embedding is None
        async with session_factory() as session:
            r = await session.get(FileRecord, file_id)
            assert r is not None
            assert r.category == "other"
            assert r.tags == "binary"
            assert r.embedding is None

    finally:
        tasks_module._storage = None


async def test_embedding_updated_on_reindex(embedding_test_env) -> None:
    """Re-indexing a file regenerates its embedding."""
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, storage = embedding_test_env

    file_id = "reindex-embed-001"
    storage_key = await storage.save(file_id, b"hello world")

    try:
        async with session_factory() as session:
            record = FileRecord(
                id=file_id,
                filename="notes.txt",
                size=11,
                content_type="text/plain",
                checksum="cc99",
                storage_key=storage_key,
                category="old_category",
                tags="old_tag",
                summary="Old summary.",
                embedding=[0.0, 0.0],
            )
            session.add(record)
            await session.commit()

        canned_enrichment = {
            "category": "document",
            "tags": "new_tag",
            "summary": "New summary.",
        }
        new_embedding = [0.5, 0.6, 0.7]

        with (
            patch.object(
                tasks_module, "enrich_file", new=AsyncMock(return_value=canned_enrichment)
            ),
            patch.object(tasks_module, "generate_embedding", return_value=new_embedding),
        ):
            tasks_module._storage = storage

            from src.robotsix_file_hub.tasks import EnrichmentJob

            job = EnrichmentJob(
                file_id=file_id,
                storage_key=storage_key,
                content_type="text/plain",
            )
            success = await tasks_module._process_enrichment(job)
            assert success is True

        # Embedding should be overwritten with the new value
        async with session_factory() as session:
            r = await session.get(FileRecord, file_id)
            assert r is not None
            assert r.embedding == new_embedding
            assert r.category == "document"
            assert r.tags == "new_tag"

    finally:
        tasks_module._storage = None
