"""Tests for the file delete endpoint."""

import io
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.robotsix_file_hub.models import FileRecord
from src.robotsix_file_hub.storage import StorageBackend, StorageError


@pytest.fixture
async def uploaded_file(
    test_client: AsyncClient,
    test_db_session: AsyncSession,
) -> FileRecord:
    """Upload a file and return its DB record for delete tests."""
    content = b"delete me"
    response = await test_client.post(
        "/files",
        files={"file": ("delete-me.txt", io.BytesIO(content), "text/plain")},
    )
    assert response.status_code == 200
    file_id = response.json()["id"]
    record = await test_db_session.get(FileRecord, file_id)
    assert record is not None
    return record


async def test_delete_file_with_header_confirm(
    test_client: AsyncClient,
    uploaded_file: FileRecord,
) -> None:
    """DELETE /files/{id} with X-Confirm-Delete header returns 204."""
    response = await test_client.delete(
        f"/files/{uploaded_file.id}",
        headers={"X-Confirm-Delete": "true"},
    )
    assert response.status_code == 204
    assert response.content == b""


async def test_delete_file_with_query_param_confirm(
    test_client: AsyncClient,
    uploaded_file: FileRecord,
) -> None:
    """DELETE /files/{id} with ?confirm=true returns 204."""
    response = await test_client.delete(
        f"/files/{uploaded_file.id}?confirm=true",
    )
    assert response.status_code == 204


async def test_delete_file_missing_confirmation(
    test_client: AsyncClient,
    uploaded_file: FileRecord,
) -> None:
    """DELETE /files/{id} without confirmation returns 400."""
    response = await test_client.delete(f"/files/{uploaded_file.id}")
    assert response.status_code == 400
    assert "confirmation" in response.json()["detail"].lower()


async def test_delete_file_removes_db_record(
    test_client: AsyncClient,
    test_db_session: AsyncSession,
    uploaded_file: FileRecord,
) -> None:
    """DELETE /files/{id} removes the database record."""
    response = await test_client.delete(
        f"/files/{uploaded_file.id}",
        headers={"X-Confirm-Delete": "true"},
    )
    assert response.status_code == 204
    # Expunge the cached record so session.get() hits the database
    # rather than returning the stale identity-map entry.
    test_db_session.expunge(uploaded_file)
    record = await test_db_session.get(FileRecord, uploaded_file.id)
    assert record is None


async def test_delete_file_removes_storage_bytes(
    test_client: AsyncClient,
    test_storage: StorageBackend,
    uploaded_file: FileRecord,
) -> None:
    """DELETE /files/{id} removes stored file bytes."""
    # Verify bytes exist before delete
    content = await test_storage.get(uploaded_file.storage_key)
    assert content is not None

    response = await test_client.delete(
        f"/files/{uploaded_file.id}",
        headers={"X-Confirm-Delete": "true"},
    )
    assert response.status_code == 204

    # Verify bytes are gone
    with pytest.raises(StorageError):
        await test_storage.get(uploaded_file.storage_key)


async def test_delete_file_not_found(test_client: AsyncClient) -> None:
    """DELETE /files/{id} with unknown id returns 404."""
    fake_id = str(uuid.uuid4())
    response = await test_client.delete(
        f"/files/{fake_id}",
        headers={"X-Confirm-Delete": "true"},
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


async def test_delete_file_wrong_confirmation_value(
    test_client: AsyncClient,
    uploaded_file: FileRecord,
) -> None:
    """DELETE /files/{id} with wrong confirmation value returns 400."""
    response = await test_client.delete(
        f"/files/{uploaded_file.id}",
        headers={"X-Confirm-Delete": "false"},
    )
    assert response.status_code == 400
