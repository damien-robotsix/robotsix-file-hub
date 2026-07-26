"""Tests for file download, metadata, and listing endpoints."""

import io
import os
import tempfile
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.robotsix_file_hub.config import Settings
from src.robotsix_file_hub.database import get_db
from src.robotsix_file_hub.main import app
from src.robotsix_file_hub.models import Base, FileRecord
from src.robotsix_file_hub.storage import LocalStorageBackend


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
    assert "storage_key" in data


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


# ---------------------------------------------------------------------------
# Helper: create dummy files and populate the DB with given FileRecord list
# ---------------------------------------------------------------------------
async def _seed_records(
    tmp_upload_dir: str,
    session_factory: async_sessionmaker[AsyncSession],
    records: list[FileRecord],
) -> None:
    """Create placeholder files on disk and insert FileRecords into the DB."""
    for rec in records:
        p = os.path.join(tmp_upload_dir, rec.filename)
        with open(p, "wb") as f:
            f.write(b"x")
        rec.storage_key = p
    async with session_factory() as session:
        session.add_all(records)
        await session.commit()


# ---------------------------------------------------------------------------
# Category filter
# ---------------------------------------------------------------------------


async def test_list_files_category_filter(tmp_upload_dir: str) -> None:
    """GET /files?category=... filters by exact category match."""
    import src.robotsix_file_hub.routes.files as routes_module

    storage = LocalStorageBackend(base_path=tmp_upload_dir)
    routes_module._storage = storage

    db_path = os.path.join(tmp_upload_dir, "test.db")
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    now = datetime.now(UTC)
    await _seed_records(
        tmp_upload_dir,
        session_factory,
        [
            FileRecord(
                filename="doc_a.txt",
                size=1,
                content_type="text/plain",
                checksum="a1",
                category="documents",
                tags="important",
                created_at=now,
            ),
            FileRecord(
                filename="doc_b.txt",
                size=1,
                content_type="text/plain",
                checksum="b2",
                category="documents",
                tags="draft",
                created_at=now,
            ),
            FileRecord(
                filename="img_c.png",
                size=1,
                content_type="image/png",
                checksum="c3",
                category="images",
                tags="photo",
                created_at=now,
            ),
        ],
    )

    async def override_get_db() -> AsyncSession:  # type: ignore[misc]
        async with session_factory() as session:
            yield session

    original_deps = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/files", params={"category": "documents"})

    app.dependency_overrides = original_deps
    routes_module._storage = None
    await engine.dispose()

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    filenames = {f["filename"] for f in data["files"]}
    assert filenames == {"doc_a.txt", "doc_b.txt"}


# ---------------------------------------------------------------------------
# Tag filter
# ---------------------------------------------------------------------------


async def test_list_files_tag_filter(tmp_upload_dir: str) -> None:
    """GET /files?tag=... filters by tag substring match."""
    import src.robotsix_file_hub.routes.files as routes_module

    storage = LocalStorageBackend(base_path=tmp_upload_dir)
    routes_module._storage = storage

    db_path = os.path.join(tmp_upload_dir, "test.db")
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    now = datetime.now(UTC)
    await _seed_records(
        tmp_upload_dir,
        session_factory,
        [
            FileRecord(
                filename="a.txt",
                size=1,
                content_type="text/plain",
                checksum="aa",
                category="docs",
                tags="urgent,review",
                created_at=now,
            ),
            FileRecord(
                filename="b.txt",
                size=1,
                content_type="text/plain",
                checksum="bb",
                category="docs",
                tags="review,later",
                created_at=now,
            ),
            FileRecord(
                filename="c.txt",
                size=1,
                content_type="text/plain",
                checksum="cc",
                category="misc",
                tags="archive",
                created_at=now,
            ),
        ],
    )

    async def override_get_db() -> AsyncSession:  # type: ignore[misc]
        async with session_factory() as session:
            yield session

    original_deps = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/files", params={"tag": "review"})

    app.dependency_overrides = original_deps
    routes_module._storage = None
    await engine.dispose()

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    filenames = {f["filename"] for f in data["files"]}
    assert filenames == {"a.txt", "b.txt"}


# ---------------------------------------------------------------------------
# Date-before filter
# ---------------------------------------------------------------------------


async def test_list_files_date_before_filter(tmp_upload_dir: str) -> None:
    """GET /files?before=... filters files created before a date."""
    import src.robotsix_file_hub.routes.files as routes_module

    storage = LocalStorageBackend(base_path=tmp_upload_dir)
    routes_module._storage = storage

    db_path = os.path.join(tmp_upload_dir, "test.db")
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    t0 = datetime(2025, 1, 10, tzinfo=UTC)
    t1 = datetime(2025, 1, 15, tzinfo=UTC)
    t2 = datetime(2025, 1, 20, tzinfo=UTC)
    await _seed_records(
        tmp_upload_dir,
        session_factory,
        [
            FileRecord(
                filename="old.txt",
                size=1,
                content_type="text/plain",
                checksum="o1",
                category=None,
                tags=None,
                created_at=t0,
            ),
            FileRecord(
                filename="mid.txt",
                size=1,
                content_type="text/plain",
                checksum="m1",
                category=None,
                tags=None,
                created_at=t1,
            ),
            FileRecord(
                filename="new.txt",
                size=1,
                content_type="text/plain",
                checksum="n1",
                category=None,
                tags=None,
                created_at=t2,
            ),
        ],
    )

    async def override_get_db() -> AsyncSession:  # type: ignore[misc]
        async with session_factory() as session:
            yield session

    original_deps = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db

    cutoff = "2025-01-17T00:00:00Z"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/files", params={"before": cutoff})

    app.dependency_overrides = original_deps
    routes_module._storage = None
    await engine.dispose()

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    filenames = {f["filename"] for f in data["files"]}
    assert filenames == {"old.txt", "mid.txt"}


# ---------------------------------------------------------------------------
# Date-after filter
# ---------------------------------------------------------------------------


async def test_list_files_date_after_filter(tmp_upload_dir: str) -> None:
    """GET /files?after=... filters files created after a date."""
    import src.robotsix_file_hub.routes.files as routes_module

    storage = LocalStorageBackend(base_path=tmp_upload_dir)
    routes_module._storage = storage

    db_path = os.path.join(tmp_upload_dir, "test.db")
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    t0 = datetime(2025, 1, 10, tzinfo=UTC)
    t1 = datetime(2025, 1, 15, tzinfo=UTC)
    t2 = datetime(2025, 1, 20, tzinfo=UTC)
    await _seed_records(
        tmp_upload_dir,
        session_factory,
        [
            FileRecord(
                filename="old.txt",
                size=1,
                content_type="text/plain",
                checksum="o1",
                category=None,
                tags=None,
                created_at=t0,
            ),
            FileRecord(
                filename="mid.txt",
                size=1,
                content_type="text/plain",
                checksum="m1",
                category=None,
                tags=None,
                created_at=t1,
            ),
            FileRecord(
                filename="new.txt",
                size=1,
                content_type="text/plain",
                checksum="n1",
                category=None,
                tags=None,
                created_at=t2,
            ),
        ],
    )

    async def override_get_db() -> AsyncSession:  # type: ignore[misc]
        async with session_factory() as session:
            yield session

    original_deps = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db

    cutoff = "2025-01-17T00:00:00Z"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/files", params={"after": cutoff})

    app.dependency_overrides = original_deps
    routes_module._storage = None
    await engine.dispose()

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["files"][0]["filename"] == "new.txt"


# ---------------------------------------------------------------------------
# Combined filters
# ---------------------------------------------------------------------------


async def test_list_files_combined_filters(tmp_upload_dir: str) -> None:
    """GET /files combines category, tag, and date filters."""
    import src.robotsix_file_hub.routes.files as routes_module

    storage = LocalStorageBackend(base_path=tmp_upload_dir)
    routes_module._storage = storage

    db_path = os.path.join(tmp_upload_dir, "test.db")
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    t_old = datetime(2025, 1, 5, tzinfo=UTC)
    t_mid = datetime(2025, 1, 15, tzinfo=UTC)
    t_new = datetime(2025, 1, 25, tzinfo=UTC)
    await _seed_records(
        tmp_upload_dir,
        session_factory,
        [
            # Should match: docs + "review" tag + mid-range date
            FileRecord(
                filename="match.txt",
                size=1,
                content_type="text/plain",
                checksum="m1",
                category="docs",
                tags="review,urgent",
                created_at=t_mid,
            ),
            # Wrong category
            FileRecord(
                filename="wrong_cat.txt",
                size=1,
                content_type="text/plain",
                checksum="w1",
                category="images",
                tags="review",
                created_at=t_mid,
            ),
            # Wrong tag
            FileRecord(
                filename="wrong_tag.txt",
                size=1,
                content_type="text/plain",
                checksum="w2",
                category="docs",
                tags="draft",
                created_at=t_mid,
            ),
            # Too old
            FileRecord(
                filename="too_old.txt",
                size=1,
                content_type="text/plain",
                checksum="w3",
                category="docs",
                tags="review",
                created_at=t_old,
            ),
            # Too new
            FileRecord(
                filename="too_new.txt",
                size=1,
                content_type="text/plain",
                checksum="w4",
                category="docs",
                tags="review",
                created_at=t_new,
            ),
        ],
    )

    async def override_get_db() -> AsyncSession:  # type: ignore[misc]
        async with session_factory() as session:
            yield session

    original_deps = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/files",
            params={
                "category": "docs",
                "tag": "review",
                "after": "2025-01-10T00:00:00Z",
                "before": "2025-01-20T00:00:00Z",
            },
        )

    app.dependency_overrides = original_deps
    routes_module._storage = None
    await engine.dispose()

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["files"][0]["filename"] == "match.txt"


# ---------------------------------------------------------------------------
# Authentication tests
# ---------------------------------------------------------------------------


async def test_auth_disabled_when_token_empty(test_client: AsyncClient) -> None:
    """When auth_token is empty, requests without auth headers succeed."""
    response = await test_client.get("/files")
    assert response.status_code == 200


async def test_auth_missing_token_returns_401(test_client: AsyncClient) -> None:
    """When auth_token is set, unauthenticated requests return 401."""
    app.dependency_overrides[Settings] = lambda: Settings(auth_token="secret")
    try:
        response = await test_client.get("/files")
    finally:
        app.dependency_overrides.pop(Settings, None)

    assert response.status_code == 401
    assert "detail" in response.json()


async def test_auth_wrong_token_returns_403(test_client: AsyncClient) -> None:
    """When auth_token is set, requests with wrong token return 403."""
    app.dependency_overrides[Settings] = lambda: Settings(auth_token="secret")
    try:
        response = await test_client.get("/files", headers={"Authorization": "Bearer wrong"})
    finally:
        app.dependency_overrides.pop(Settings, None)

    assert response.status_code == 403
    assert "detail" in response.json()


async def test_auth_correct_token_succeeds(test_client: AsyncClient) -> None:
    """When auth_token is set, requests with correct token succeed."""
    app.dependency_overrides[Settings] = lambda: Settings(auth_token="secret")
    try:
        response = await test_client.get("/files", headers={"Authorization": "Bearer secret"})
    finally:
        app.dependency_overrides.pop(Settings, None)

    assert response.status_code == 200


async def test_auth_required_on_download_endpoint(test_client: AsyncClient) -> None:
    """Auth is enforced on GET /files/{id}."""
    app.dependency_overrides[Settings] = lambda: Settings(auth_token="secret")
    try:
        response = await test_client.get("/files/some-id")
    finally:
        app.dependency_overrides.pop(Settings, None)

    assert response.status_code == 401


async def test_auth_required_on_metadata_endpoint(test_client: AsyncClient) -> None:
    """Auth is enforced on GET /files/{id}/metadata."""
    app.dependency_overrides[Settings] = lambda: Settings(auth_token="secret")
    try:
        response = await test_client.get("/files/some-id/metadata")
    finally:
        app.dependency_overrides.pop(Settings, None)

    assert response.status_code == 401


async def test_auth_correct_token_on_download(test_client: AsyncClient) -> None:
    """Correct auth token allows access to download endpoint."""
    content = b"auth test content"
    upload_resp = await test_client.post(
        "/files",
        files={"file": ("auth_test.txt", io.BytesIO(content), "text/plain")},
    )
    assert upload_resp.status_code == 200
    file_id = upload_resp.json()["id"]

    app.dependency_overrides[Settings] = lambda: Settings(auth_token="secret")
    try:
        response = await test_client.get(
            f"/files/{file_id}",
            headers={"Authorization": "Bearer secret"},
        )
    finally:
        app.dependency_overrides.pop(Settings, None)

    assert response.status_code == 200
    assert response.content == content


async def test_auth_correct_token_on_list(test_client: AsyncClient) -> None:
    """Correct auth token allows access to list endpoint."""
    app.dependency_overrides[Settings] = lambda: Settings(auth_token="secret")
    try:
        response = await test_client.get("/files", headers={"Authorization": "Bearer secret"})
    finally:
        app.dependency_overrides.pop(Settings, None)

    assert response.status_code == 200
