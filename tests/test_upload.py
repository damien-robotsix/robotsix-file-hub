"""Tests for file upload endpoints."""

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


async def test_upload_single_file(tmp_upload_dir: str) -> None:
    """POST /files with a single file returns metadata."""
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
        content = b"hello world"
        response = await client.post(
            "/files",
            files={"file": ("test.txt", io.BytesIO(content), "text/plain")},
        )

    app.dependency_overrides = original_deps
    routes_module._storage = None
    await engine.dispose()

    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "test.txt"
    assert data["size"] == len(content)
    assert data["content_type"] == "text/plain"
    assert len(data["checksum"]) == 64
    assert "id" in data
    assert "created_at" in data


async def test_upload_file_too_large(tmp_upload_dir: str) -> None:
    """POST /files with a file exceeding max size returns 413."""
    import src.robotsix_file_hub.routes.files as routes_module

    storage = LocalStorageBackend(base_path=tmp_upload_dir)
    routes_module._storage = storage

    original_max = routes_module.MAX_FILE_SIZE
    routes_module.MAX_FILE_SIZE = 5

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
        content = b"this is too large"
        response = await client.post(
            "/files",
            files={"file": ("big.txt", io.BytesIO(content), "text/plain")},
        )

    app.dependency_overrides = original_deps
    routes_module.MAX_FILE_SIZE = original_max
    routes_module._storage = None
    await engine.dispose()

    assert response.status_code == 413


async def test_upload_batch(tmp_upload_dir: str) -> None:
    """POST /files/batch with multiple files returns all metadata."""
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
        files = [
            ("files", ("a.txt", io.BytesIO(b"aaa"), "text/plain")),
            ("files", ("b.txt", io.BytesIO(b"bbbbb"), "text/plain")),
        ]
        response = await client.post("/files/batch", files=files)

    app.dependency_overrides = original_deps
    routes_module._storage = None
    await engine.dispose()

    assert response.status_code == 200
    data = response.json()
    assert len(data["files"]) == 2
    assert data["files"][0]["filename"] == "a.txt"
    assert data["files"][0]["size"] == 3
    assert data["files"][1]["filename"] == "b.txt"
    assert data["files"][1]["size"] == 5
