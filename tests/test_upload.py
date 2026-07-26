"""Tests for file upload endpoints."""

import io
import os
import tempfile

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.robotsix_file_hub.database import get_db
from src.robotsix_file_hub.main import app
from src.robotsix_file_hub.models import Base, FileRecord
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


async def test_upload_batch_partial_failure_rolls_back(
    tmp_upload_dir: str,
) -> None:
    """A mid-batch failure rolls back all prior files in the batch.

    When one file in a batch exceeds ``MAX_FILE_SIZE``, every file
    that was already processed must be rolled back — no database
    records and no stored bytes may remain.
    """
    import src.robotsix_file_hub.routes.files as routes_module

    storage = LocalStorageBackend(base_path=tmp_upload_dir)
    routes_module._storage = storage

    original_max = routes_module.MAX_FILE_SIZE
    routes_module.MAX_FILE_SIZE = 5  # only very small files are accepted

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
        # First file is small (3 bytes → OK), second is too large (7 bytes → 413).
        files = [
            ("files", ("ok.txt", io.BytesIO(b"abc"), "text/plain")),
            ("files", ("big.txt", io.BytesIO(b"too big"), "text/plain")),
        ]
        response = await client.post("/files/batch", files=files)

    app.dependency_overrides = original_deps
    routes_module.MAX_FILE_SIZE = original_max
    routes_module._storage = None

    # The batch must fail.
    assert response.status_code == 413

    # --- Verify no DB records remain ---
    async with session_factory() as session:
        result = await session.execute(select(FileRecord))
        rows = result.scalars().all()
        assert len(rows) == 0, f"Expected 0 DB rows after rollback, got {len(rows)}"

    # --- Verify no storage files remain (ignore the test database) ---
    stored = [p for p in storage.base_path.iterdir() if p.suffix != ".db"]
    assert len(stored) == 0, f"Expected 0 stored files after rollback, got {len(stored)}"

    await engine.dispose()
