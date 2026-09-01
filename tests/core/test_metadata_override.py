"""Tests for the curated metadata-override endpoint and its clobber-protection.

Covers ``PATCH /files/{id}/metadata`` (write + provenance marker + validation)
and the guarantee that a later automatic enrichment pass does not overwrite
agent/manual-curated values unless explicitly forced.
"""

import io
from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.robotsix_file_hub.models import FileRecord
from src.robotsix_file_hub.tasks import EnrichmentJob, _process_enrichment


async def _upload(test_client: AsyncClient) -> str:
    """Upload a dummy file and return its id."""
    resp = await test_client.post(
        "/files",
        files={"file": ("curated.txt", io.BytesIO(b"some file content"), "text/plain")},
    )
    assert resp.status_code == 200
    return resp.json()["id"]


# ── PATCH /files/{id}/metadata ─────────────────────────────────────


async def test_patch_metadata_sets_fields_and_source(test_client: AsyncClient) -> None:
    """A partial PATCH stores the given fields and defaults the source marker."""
    file_id = await _upload(test_client)

    response = await test_client.patch(
        f"/files/{file_id}/metadata",
        json={"summary": "A curated summary", "category": "legal", "tags": ["contract", "2024"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["summary"] == "A curated summary"
    assert data["category"] == "legal"
    assert data["tags"] == "contract,2024"
    assert data["metadata_source"] == "manual"

    # The normal read path returns the same curated values.
    read = await test_client.get(f"/files/{file_id}/metadata")
    assert read.status_code == 200
    got = read.json()
    assert got["summary"] == "A curated summary"
    assert got["category"] == "legal"
    assert got["tags"] == "contract,2024"
    assert got["metadata_source"] == "manual"


async def test_patch_metadata_partial_keeps_others_and_null_clears(
    test_client: AsyncClient, test_db_session: AsyncSession
) -> None:
    """Omitted fields are left unchanged and an explicit null clears a field."""
    file_id = await _upload(test_client)

    await test_client.patch(
        f"/files/{file_id}/metadata",
        json={"summary": "First", "category": "doc", "tags": ["a", "b"]},
    )
    # Only update category; summary and tags are untouched.
    resp = await test_client.patch(f"/files/{file_id}/metadata", json={"category": "research"})
    assert resp.status_code == 200
    assert resp.json()["category"] == "research"
    assert resp.json()["summary"] == "First"
    assert resp.json()["tags"] == "a,b"
    assert resp.json()["metadata_source"] == "manual"

    # Explicit null clears the summary field.
    resp = await test_client.patch(f"/files/{file_id}/metadata", json={"summary": None})
    assert resp.status_code == 200
    assert resp.json()["summary"] is None
    assert resp.json()["category"] == "research"


async def test_patch_metadata_agent_source(test_client: AsyncClient) -> None:
    """A caller may declare the provenance as ``agent``."""
    file_id = await _upload(test_client)

    response = await test_client.patch(
        f"/files/{file_id}/metadata",
        json={"summary": "Agent curated", "metadata_source": "agent"},
    )

    assert response.status_code == 200
    assert response.json()["summary"] == "Agent curated"
    assert response.json()["metadata_source"] == "agent"


async def test_patch_metadata_not_found(test_client: AsyncClient) -> None:
    """PATCH on an unknown file id returns 404."""
    response = await test_client.patch(
        "/files/nonexistent-id/metadata",
        json={"summary": "nope"},
    )
    assert response.status_code == 404


async def test_patch_metadata_empty_body_400(test_client: AsyncClient) -> None:
    """A request that changes nothing (no data fields) returns 400."""
    file_id = await _upload(test_client)

    response = await test_client.patch(f"/files/{file_id}/metadata", json={})
    assert response.status_code == 400

    # A body with only a source marker and no data change is also rejected.
    response = await test_client.patch(
        f"/files/{file_id}/metadata", json={"metadata_source": "agent"}
    )
    assert response.status_code == 400


async def test_patch_metadata_invalid_body_422(test_client: AsyncClient) -> None:
    """A body with the wrong shape (non-string summary) is a 422 validation error."""
    file_id = await _upload(test_client)

    response = await test_client.patch(f"/files/{file_id}/metadata", json={"summary": 1234})
    assert response.status_code == 422

    # Empty tags entries are rejected.
    response = await test_client.patch(
        f"/files/{file_id}/metadata", json={"tags": ["ok", "", "bad"]}
    )
    assert response.status_code == 422


# ── Clobber-protection: enrichment must not overwrite curated values ──


async def _run_enrichment(
    session_factory, storage, file_id: str, *, storage_key: str, force: bool
) -> bool:
    import src.robotsix_file_hub.storage as storage_module
    import src.robotsix_file_hub.tasks as tasks_module

    storage_module._storage = storage
    canned = {"category": "document", "tags": "pdf,report", "summary": "Model summary"}
    job = EnrichmentJob(
        file_id=file_id,
        storage_key=storage_key,
        content_type="text/plain",
        task_id="task-1",
        force=force,
    )
    try:
        with (
            patch.object(tasks_module, "enrich_file", new=AsyncMock(return_value=canned)),
            patch.object(
                tasks_module, "generate_embedding", new=AsyncMock(return_value=[0.1, 0.2])
            ),
        ):
            return await _process_enrichment(job)
    finally:
        storage_module._storage = None


async def test_enrichment_does_not_overwrite_curated_metadata(tasks_test_env) -> None:
    """A curated (agent) record is a no-op for default enrichment."""
    session_factory, storage = tasks_test_env

    file_id = "curated-file-001"
    storage_key = await storage.save(file_id, b"curated content")

    async with session_factory() as session:
        session.add(
            FileRecord(
                id=file_id,
                filename="curated.txt",
                size=15,
                content_type="text/plain",
                checksum="abc123",
                storage_key=storage_key,
                summary="Curated summary",
                category="legal",
                tags="a,b",
                metadata_source="agent",
            )
        )
        await session.commit()

    ok = await _run_enrichment(
        session_factory, storage, file_id, storage_key=storage_key, force=False
    )

    assert ok is True
    async with session_factory() as session:
        record = await session.get(FileRecord, file_id)
        assert record is not None
        # Curated values survive the enrichment pass untouched.
        assert record.summary == "Curated summary"
        assert record.category == "legal"
        assert record.tags == "a,b"
        assert record.metadata_source == "agent"


async def test_enrichment_force_overwrites_curated_metadata(tasks_test_env) -> None:
    """An explicitly forced enrichment overwrites curated values."""
    session_factory, storage = tasks_test_env

    file_id = "curated-file-002"
    storage_key = await storage.save(file_id, b"curated content")

    async with session_factory() as session:
        session.add(
            FileRecord(
                id=file_id,
                filename="curated2.txt",
                size=15,
                content_type="text/plain",
                checksum="def456",
                storage_key=storage_key,
                summary="Curated summary",
                category="legal",
                tags="a,b",
                metadata_source="manual",
            )
        )
        await session.commit()

    ok = await _run_enrichment(
        session_factory, storage, file_id, storage_key=storage_key, force=True
    )

    assert ok is True
    async with session_factory() as session:
        record = await session.get(FileRecord, file_id)
        assert record is not None
        assert record.summary == "Model summary"
        assert record.category == "document"
        assert record.tags == "pdf,report"
        assert record.metadata_source == "enrichment"
