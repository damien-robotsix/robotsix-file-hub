"""Tests for file download, metadata, and listing endpoints."""

import io

from httpx import AsyncClient


async def test_download_file(test_client: AsyncClient) -> None:
    """GET /files/{id} returns raw bytes with correct headers."""
    content = b"hello download test"
    upload_resp = await test_client.post(
        "/files",
        files={"file": ("download.txt", io.BytesIO(content), "text/plain")},
    )
    assert upload_resp.status_code == 200
    file_id = upload_resp.json()["id"]

    response = await test_client.get(f"/files/{file_id}")

    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"].startswith("text/plain")
    assert 'filename="download.txt"' in response.headers["content-disposition"]
    assert response.headers["content-length"] == str(len(content))


async def test_download_file_not_found(test_client: AsyncClient) -> None:
    """GET /files/{id} with unknown id returns 404."""
    response = await test_client.get("/files/nonexistent-id")

    assert response.status_code == 404
    assert response.json()["detail"] == "File not found"


async def test_get_file_metadata(test_client: AsyncClient) -> None:
    """GET /files/{id}/metadata returns full record as JSON."""
    content = b"metadata test content"
    upload_resp = await test_client.post(
        "/files",
        files={"file": ("meta.txt", io.BytesIO(content), "text/plain")},
    )
    assert upload_resp.status_code == 200
    file_id = upload_resp.json()["id"]

    response = await test_client.get(f"/files/{file_id}/metadata")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == file_id
    assert data["filename"] == "meta.txt"
    assert data["size"] == len(content)
    assert data["content_type"] == "text/plain"
    assert data["category"] is None
    assert data["tags"] is None
    assert data["summary"] is None
    assert data["source"] is None
    assert "created_at" in data
    assert "checksum" in data
    assert "storage_path" in data


async def test_get_file_metadata_not_found(test_client: AsyncClient) -> None:
    """GET /files/{id}/metadata with unknown id returns 404."""
    response = await test_client.get("/files/nonexistent-id/metadata")

    assert response.status_code == 404


async def test_list_files_empty(test_client: AsyncClient) -> None:
    """GET /files returns empty list when no files exist."""
    response = await test_client.get("/files")

    assert response.status_code == 200
    data = response.json()
    assert data["files"] == []
    assert data["total"] == 0
    assert data["offset"] == 0
    assert data["limit"] == 50


async def test_list_files_with_data(test_client: AsyncClient) -> None:
    """GET /files returns paginated list with all uploaded files."""
    # Upload two files
    for name in ("first.txt", "second.txt"):
        upload_resp = await test_client.post(
            "/files",
            files={"file": (name, io.BytesIO(b"data"), "text/plain")},
        )
        assert upload_resp.status_code == 200

    response = await test_client.get("/files")

    assert response.status_code == 200
    data = response.json()
    assert len(data["files"]) == 2
    assert data["total"] == 2
    assert data["offset"] == 0
    assert data["limit"] == 50
    filenames = {f["filename"] for f in data["files"]}
    assert filenames == {"first.txt", "second.txt"}


async def test_list_files_pagination(test_client: AsyncClient) -> None:
    """GET /files with offset and limit respects pagination."""
    # Upload 3 files
    for name in ("a.txt", "b.txt", "c.txt"):
        upload_resp = await test_client.post(
            "/files",
            files={"file": (name, io.BytesIO(b"x"), "text/plain")},
        )
        assert upload_resp.status_code == 200

    # Page 1: offset=0, limit=2
    resp1 = await test_client.get("/files?offset=0&limit=2")
    # Page 2: offset=2, limit=2
    resp2 = await test_client.get("/files?offset=2&limit=2")

    assert resp1.status_code == 200
    data1 = resp1.json()
    assert len(data1["files"]) == 2
    assert data1["total"] == 3
    assert data1["offset"] == 0
    assert data1["limit"] == 2

    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["files"]) == 1
    assert data2["total"] == 3
    assert data2["offset"] == 2
    assert data2["limit"] == 2


async def test_list_files_content_type_filter(test_client: AsyncClient) -> None:
    """GET /files?content_type=... filters by MIME type."""
    # Upload text and image files
    await test_client.post(
        "/files",
        files={"file": ("doc.txt", io.BytesIO(b"text"), "text/plain")},
    )
    await test_client.post(
        "/files",
        files={"file": ("img.png", io.BytesIO(b"png"), "image/png")},
    )

    response = await test_client.get("/files", params={"content_type": "text/plain"})

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["files"][0]["filename"] == "doc.txt"
