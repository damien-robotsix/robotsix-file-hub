"""Tests for embedding generation and storage."""

import uuid
from unittest.mock import AsyncMock, patch

from src.robotsix_file_hub.models import FileRecord

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


async def test_generate_embedding_delegates_to_api() -> None:
    """generate_embedding forwards to the OpenAI-compatible endpoint
    rather than loading a model in-process."""
    import src.robotsix_file_hub.embeddings as emb_module

    vector = [0.1, 0.2, 0.3]
    with patch.object(
        emb_module, "_api_generate_embedding", new=AsyncMock(return_value=vector)
    ) as api:
        result = await emb_module.generate_embedding("test text")

    assert result == vector
    api.assert_awaited_once_with("test text")


async def test_generate_embedding_returns_none_when_backend_unreachable() -> None:
    """A None from the endpoint propagates unchanged: callers degrade to
    keyword-only search rather than failing the request."""
    import src.robotsix_file_hub.embeddings as emb_module

    with patch.object(emb_module, "_api_generate_embedding", new=AsyncMock(return_value=None)):
        assert await emb_module.generate_embedding("text") is None


async def test_embedding_dimensions_match_configured_model() -> None:
    """The pgvector column width and the configured model must agree —
    a mismatch fails every insert at runtime, not at import."""
    from src.robotsix_file_hub.config import Settings
    from src.robotsix_file_hub.models import EMBEDDING_DIMENSIONS

    # bge-m3 emits 1024-dim vectors.
    assert Settings().enrichment_llm_embedding_model == "bge-m3"
    assert EMBEDDING_DIMENSIONS == 1024


# ── Integration tests ──────────────────────────────────────────────


async def test_embedding_stored_during_enrichment(tasks_test_env) -> None:
    """_process_enrichment stores the embedding on the DB record."""
    import src.robotsix_file_hub.storage as storage_module
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, storage = tasks_test_env

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
            patch.object(
                tasks_module, "generate_embedding", new=AsyncMock(return_value=fake_embedding)
            ),
        ):
            storage_module._storage = storage

            from src.robotsix_file_hub.tasks import EnrichmentJob

            job = EnrichmentJob(
                file_id=file_id,
                storage_key=storage_key,
                content_type="text/plain",
                task_id=str(uuid.uuid4()),
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
        storage_module._storage = None


async def test_embedding_null_on_generation_failure(tasks_test_env) -> None:
    """When embedding generation raises, embedding is set to None (best-effort)."""
    import src.robotsix_file_hub.storage as storage_module
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, storage = tasks_test_env

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
                new=AsyncMock(side_effect=RuntimeError("embedding backend unreachable")),
            ),
        ):
            storage_module._storage = storage

            from src.robotsix_file_hub.tasks import EnrichmentJob

            job = EnrichmentJob(
                file_id=file_id,
                storage_key=storage_key,
                content_type="application/octet-stream",
                task_id=str(uuid.uuid4()),
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
        storage_module._storage = None


async def test_embedding_updated_on_reindex(tasks_test_env) -> None:
    """Re-indexing a file regenerates its embedding."""
    import src.robotsix_file_hub.storage as storage_module
    import src.robotsix_file_hub.tasks as tasks_module

    session_factory, storage = tasks_test_env

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
            patch.object(
                tasks_module, "generate_embedding", new=AsyncMock(return_value=new_embedding)
            ),
        ):
            storage_module._storage = storage

            from src.robotsix_file_hub.tasks import EnrichmentJob

            job = EnrichmentJob(
                file_id=file_id,
                storage_key=storage_key,
                content_type="text/plain",
                task_id=str(uuid.uuid4()),
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
        storage_module._storage = None
