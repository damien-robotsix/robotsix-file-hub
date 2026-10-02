"""Startup schema reconciliation for databases created by an older release.

Regression for the 2026-09-04 production incident: migration
``0004_metadata_source`` never ran (the Alembic chain is Postgres-only,
production runs SQLite) and ``create_all`` does not alter existing
tables, so every ``/search`` SELECT referencing
``file_records.metadata_source`` failed with
``sqlite3.OperationalError: no such column``.
"""

import os
import tempfile
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.robotsix_file_hub.database import _reconcile_missing_columns
from src.robotsix_file_hub.models import Base, FileRecord

# The exact schema of the production database before migration 0004,
# as created by create_all of release 0.11.x (13 columns, no
# metadata_source).
_PRE_0004_FILE_RECORDS = """
CREATE TABLE file_records (
    id VARCHAR(36) NOT NULL,
    filename VARCHAR(512) NOT NULL,
    size INTEGER NOT NULL,
    content_type VARCHAR(256) NOT NULL,
    checksum VARCHAR(64) NOT NULL,
    storage_key VARCHAR(1024) NOT NULL,
    category VARCHAR(256),
    tags VARCHAR(1024),
    summary VARCHAR(4096),
    source VARCHAR(256),
    embedding VECTOR(1024),
    created_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
    updated_at DATETIME DEFAULT (CURRENT_TIMESTAMP) NOT NULL,
    PRIMARY KEY (id)
)
"""


@pytest.fixture
async def old_schema_engine() -> AsyncGenerator[AsyncEngine]:
    """An on-disk SQLite database holding the pre-0004 schema and one row."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "old.db")
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        async with engine.begin() as conn:
            await conn.execute(text(_PRE_0004_FILE_RECORDS))
            await conn.execute(
                text(
                    "INSERT INTO file_records "
                    "(id, filename, size, content_type, checksum, storage_key, summary) "
                    "VALUES ('old-row', 'images.zip', 1, 'application/zip', 'c', 'k', "
                    "'a bundle of images')"
                )
            )
        yield engine
        await engine.dispose()


async def test_reconcile_adds_missing_column_and_search_select_works(
    old_schema_engine: AsyncEngine,
) -> None:
    async with old_schema_engine.begin() as conn:
        await conn.run_sync(_reconcile_missing_columns)
        await conn.run_sync(Base.metadata.create_all)

    # The failing production query shape: a full-entity SELECT with LIKE
    # filters, which references every ORM column including metadata_source.
    async with old_schema_engine.connect() as conn:
        result = await conn.execute(
            select(FileRecord).where(
                FileRecord.filename.like("%images%")
                | FileRecord.summary.like("%images%")
                | FileRecord.tags.like("%images%")
            )
        )
        rows = result.fetchall()
    assert len(rows) == 1
    assert rows[0].metadata_source is None


async def test_reconcile_is_idempotent(old_schema_engine: AsyncEngine) -> None:
    for _ in range(2):
        async with old_schema_engine.begin() as conn:
            await conn.run_sync(_reconcile_missing_columns)
            await conn.run_sync(Base.metadata.create_all)

    async with old_schema_engine.connect() as conn:
        cols = await conn.execute(text("PRAGMA table_info(file_records)"))
        names = [row[1] for row in cols.fetchall()]
    assert names.count("metadata_source") == 1


async def test_reconcile_noop_on_fresh_database() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(_reconcile_missing_columns)
        async with engine.connect() as conn:
            cols = await conn.execute(text("PRAGMA table_info(file_records)"))
            names = {row[1] for row in cols.fetchall()}
        assert "metadata_source" in names
    finally:
        await engine.dispose()
