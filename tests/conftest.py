"""Shared pytest fixtures for the robotsix-file-hub test suite."""

import os
import tempfile
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.robotsix_file_hub.database import get_db
from src.robotsix_file_hub.main import app
from src.robotsix_file_hub.models import Base
from src.robotsix_file_hub.storage import LocalStorageBackend, StorageBackend


@pytest.fixture
def tmp_upload_dir() -> str:
    """Temporary directory for file storage during tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
async def test_engine(tmp_upload_dir: str):
    """In-memory / on-disk SQLite async engine with tables created.

    Uses a temp-directory-backed SQLite file so that multiple
    connections can share the same database during a test.
    """
    db_path = os.path.join(tmp_upload_dir, "test.db")
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def test_session_factory(test_engine):
    """Async session factory bound to the test engine."""
    return async_sessionmaker(test_engine, expire_on_commit=False)


@pytest.fixture
def test_storage(tmp_upload_dir: str) -> StorageBackend:
    """Local storage backend pointing at the temp directory.

    Also patches the routes module's module-level ``_storage`` so
    endpoint handlers pick it up without a real S3/MinIO backend.
    """
    import src.robotsix_file_hub.storage as storage_module

    storage = LocalStorageBackend(base_path=tmp_upload_dir)
    storage_module._storage = storage
    try:
        yield storage
    finally:
        storage_module._storage = None


@pytest.fixture
async def test_db_session(test_session_factory):
    """AsyncSession for seeding test data before endpoint calls.

    Uses the same engine as ``test_client`` so pre-populated data
    is visible to HTTP requests made through the test client.
    """
    async with test_session_factory() as session:
        yield session


@pytest.fixture
async def test_client(
    test_session_factory,
    test_storage: StorageBackend,
) -> AsyncGenerator[AsyncClient]:
    """Async HTTP client wired to the FastAPI app with test overrides.

    Injects a test database session and local storage backend so
    that endpoint tests run against an isolated environment.
    """

    async def override_get_db() -> AsyncGenerator[AsyncSession]:
        async with test_session_factory() as session:
            yield session

    original_deps = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides = original_deps


@pytest.fixture
def mock_enrich_file() -> dict[str, str | None]:
    """Patch ``tasks.enrich_file`` to return canned enrichment values.

    Yields the canned dict so tests can assert against the values that
    the (mocked) enrichment pipeline would produce.
    """
    import src.robotsix_file_hub.tasks as tasks_module

    canned: dict[str, str | None] = {
        "category": "document",
        "tags": "test,mock",
        "summary": "Mock enrichment summary.",
        "embedding": "[0.1, 0.2, 0.3]",
    }

    with patch.object(tasks_module, "enrich_file", new=AsyncMock(return_value=canned)):
        yield canned


@pytest.fixture
def mock_search_embedding() -> list[float]:
    """Patch ``search.generate_embedding_async`` to return a canned vector.

    Prevents real embedding API calls during search endpoint tests.
    """
    import src.robotsix_file_hub.search as search_module

    canned = [0.1, 0.2, 0.3]
    with patch.object(
        search_module, "generate_embedding_async", new=AsyncMock(return_value=canned)
    ):
        yield canned


@pytest.fixture
async def tasks_test_env(
    test_session_factory,
    test_storage: StorageBackend,
):
    """Session factory + storage + monkey-patch of ``tasks.async_session_factory``.

    Composes the shared ``test_session_factory`` and ``test_storage`` fixtures
    and monkey-patches ``src.robotsix_file_hub.tasks.async_session_factory``
    so worker / reindex / embedding tests share one environment.

    Yields ``(session_factory, storage)``.  Teardown restores the original
    ``async_session_factory``, clears module-level reindex state, and
    nulls out ``storage._storage``.
    """
    import src.robotsix_file_hub.storage as storage_module
    import src.robotsix_file_hub.tasks as tasks_module

    original_session_factory = tasks_module.async_session_factory
    tasks_module.async_session_factory = test_session_factory  # type: ignore[assignment]

    try:
        yield test_session_factory, test_storage
    finally:
        storage_module._storage = None
        tasks_module.async_session_factory = original_session_factory
        tasks_module._reindex_total = 0
        tasks_module._reindex_completed = 0
        tasks_module._reindex_failed = 0
        tasks_module._reindex_active = False
