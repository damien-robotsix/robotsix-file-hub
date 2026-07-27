"""Tests for file deletion endpoint."""

import io
import os

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.robotsix_file_hub.models import FileRecord


async def test_delete_file_success(
    test_client: AsyncClient,
    test_db_session: AsyncSession,
    tmp_upload_dir: str,
) -> None:
    """DELETE /files/{id} removes the file record and stored bytes."""
    content = b"delete me"
    upload_resp = await test_client.post(
        "/files",
        files={"file": ("to_delete.txt", io.BytesIO(content), "text/plain")},
    )
    assert upload_resp.status_code == 200
    file_id = upload_resp.json()["id"]

    response = await test_client.delete(f"/files/{file_id}")

    assert response.status_code == 204
    assert response.content == b""

    # Verify DB record was removed
    record = await test_db_session.get(FileRecord, file_id)
    assert record is None

    # Verify storage file was removed
    non_db_files = [f for f in os.listdir(tmp_upload_dir) if not f.endswith(".db")]
    assert len(non_db_files) == 0, f"Expected 0 stored files after delete, got {len(non_db_files)}"


async def test_delete_file_not_found(test_client: AsyncClient) -> None:
    """DELETE /files/{id} with unknown id returns 404."""
    response = await test_client.delete("/files/nonexistent-id")

    assert response.status_code == 404
    assert response.json()["detail"] == "File not found"


async def test_delete_file_then_download_fails(test_client: AsyncClient) -> None:
    """After deleting a file, downloading it returns 404."""
    content = b"download then delete"
    upload_resp = await test_client.post(
        "/files",
        files={"file": ("temp.txt", io.BytesIO(content), "text/plain")},
    )
    assert upload_resp.status_code == 200
    file_id = upload_resp.json()["id"]

    # Delete it
    del_resp = await test_client.delete(f"/files/{file_id}")
    assert del_resp.status_code == 204

    # Download must 404
    get_resp = await test_client.get(f"/files/{file_id}")
    assert get_resp.status_code == 404


async def test_delete_file_then_metadata_fails(test_client: AsyncClient) -> None:
    """After deleting a file, metadata endpoint returns 404."""
    content = b"metadata then delete"
    upload_resp = await test_client.post(
        "/files",
        files={"file": ("meta_del.txt", io.BytesIO(content), "text/plain")},
    )
    assert upload_resp.status_code == 200
    file_id = upload_resp.json()["id"]

    del_resp = await test_client.delete(f"/files/{file_id}")
    assert del_resp.status_code == 204

    meta_resp = await test_client.get(f"/files/{file_id}/metadata")
    assert meta_resp.status_code == 404


async def test_delete_file_then_not_in_list(test_client: AsyncClient) -> None:
    """After deleting a file, it does not appear in the file list."""
    # Upload two files
    for name in ("keep.txt", "remove.txt"):
        upload_resp = await test_client.post(
            "/files",
            files={"file": (name, io.BytesIO(b"data"), "text/plain")},
        )
        assert upload_resp.status_code == 200

    # List before delete
    list_before = await test_client.get("/files")
    assert list_before.json()["total"] == 2
    remove_id = None
    for f in list_before.json()["files"]:
        if f["filename"] == "remove.txt":
            remove_id = f["id"]
            break
    assert remove_id is not None

    # Delete one
    del_resp = await test_client.delete(f"/files/{remove_id}")
    assert del_resp.status_code == 204

    # List after delete
    list_after = await test_client.get("/files")
    data = list_after.json()
    assert data["total"] == 1
    assert data["files"][0]["filename"] == "keep.txt"
