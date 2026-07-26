"""Tests for file download, metadata, and listing endpoints."""

import io
import os
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.robotsix_file_hub.database import get_db
from src.robotsix_file_hub.main import app
from src.robotsix_file_hub.models import Base
from src.robotsix_file_hub.storage import LocalStorageBackend


@pytest.fixture
def tmp_upload_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


async def test_download_file(tmp_upload_dir: str) -> None:
    """GET /files/{id} returns raw bytes with correct headers."""
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Upload a file first
        content = b"hello download test"
        upload_resp = await client.post(
            "/files",
            files={"file": ("download.txt", io.BytesIO(content), "text/plain")},
        )
        assert upload_resp.status_code == 200
        file_id = upload_resp.json()["id"]

        # Download it
        response = await client.get(f"/files/{file_id}")

    app.dependency_overrides = original_deps
    routes_module._storage = None
    await engine.dispose()

    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"].startswith("text/plain")
    assert 'filename="download.txt"' in response.headers["content-disposition"]
    assert response.headers["content-length"] == str(len(content))


async def test_download_file_not_found(tmp_upload_dir: str) -> None:
    """GET /files/{id} with unknown id returns 404."""
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/files/nonexistent-id")

    app.dependency_overrides = original_deps
    routes_module._storage = None
    await engine.dispose()

    assert response.status_code == 404
    assert response.json()["detail"] == "File not found"


async def test_get_file_metadata(tmp_upload_dir: str) -> None:
    """GET /files/{id}/metadata returns full record as JSON."""
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Upload a file first
        content = b"metadata test content"
        upload_resp = await client.post(
            "/files",
            files={"file": ("meta.txt", io.BytesIO(content), "text/plain")},
        )
        assert upload_resp.status_code == 200
        file_id = upload_resp.json()["id"]

        # Get metadata
        response = await client.get(f"/files/{file_id}/metadata")

    app.dependency_overrides = original_deps
    routes_module._storage = None
    await engine.dispose()

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


async def test_get_file_metadata_not_found(tmp_upload_dir: str) -> None:
    """GET /files/{id}/metadata with unknown id returns 404."""
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/files/nonexistent-id/metadata")

    app.dependency_overrides = original_deps
    routes_module._storage = None
    await engine.dispose()

    assert response.status_code == 404


async def test_list_files_empty(tmp_upload_dir: str) -> None:
    """GET /files returns empty list when no files exist."""
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/files")

    app.dependency_overrides = original_deps
    routes_module._storage = None
    await engine.dispose()

    assert response.status_code == 200
    data = response.json()
    assert data["files"] == []
    assert data["total"] == 0
    assert data["offset"] == 0
    assert data["limit"] == 50


async def test_list_files_with_data(tmp_upload_dir: str) -> None:
    """GET /files returns paginated list with all uploaded files."""
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Upload two files
        for name in ("first.txt", "second.txt"):
            upload_resp = await client.post(
                "/files",
                files={"file": (name, io.BytesIO(b"data"), "text/plain")},
            )
            assert upload_resp.status_code == 200

        # List all
        response = await client.get("/files")

    app.dependency_overrides = original_deps
    routes_module._storage = None
    await engine.dispose()

    assert response.status_code == 200
    data = response.json()
    assert len(data["files"]) == 2
    assert data["total"] == 2
    assert data["offset"] == 0
    assert data["limit"] == 50
    filenames = {f["filename"] for f in data["files"]}
    assert filenames == {"first.txt", "second.txt"}


async def test_list_files_pagination(tmp_upload_dir: str) -> None:
    """GET /files with offset and limit respects pagination."""
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Upload 3 files
        for name in ("a.txt", "b.txt", "c.txt"):
            upload_resp = await client.post(
                "/files",
                files={"file": (name, io.BytesIO(b"x"), "text/plain")},
            )
            assert upload_resp.status_code == 200

        # Page 1: offset=0, limit=2
        resp1 = await client.get("/files?offset=0&limit=2")
        # Page 2: offset=2, limit=2
        resp2 = await client.get("/files?offset=2&limit=2")

    app.dependency_overrides = original_deps
    routes_module._storage = None
    await engine.dispose()

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


async def test_list_files_content_type_filter(tmp_upload_dir: str) -> None:
    """GET /files?content_type=... filters by MIME type."""
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Upload text and image files
        await client.post(
            "/files",
            files={"file": ("doc.txt", io.BytesIO(b"text"), "text/plain")},
        )
        await client.post(
            "/files",
            files={"file": ("img.png", io.BytesIO(b"png"), "image/png")},
        )

        # Filter by text/plain
        response = await client.get("/files", params={"content_type": "text/plain"})

    app.dependency_overrides = original_deps
    routes_module._storage = None
    await engine.dispose()

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["files"][0]["filename"] == "doc.txt"
