"""Async SQLAlchemy database engine and session factory."""

import logging
from collections.abc import AsyncGenerator

from sqlalchemy import Connection, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

engine = create_async_engine(settings.database_url, echo=False)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Yield an async database session."""
    try:
        async with async_session_factory() as session:
            yield session
    except Exception as exc:
        logger.error("Database session error: %s", exc)
        raise


def _reconcile_missing_columns(conn: Connection) -> None:
    """Add ORM-declared columns missing from already-existing tables.

    ``create_all`` creates missing tables but never alters existing ones,
    and the Alembic chain is Postgres-only (pgvector) while deployments
    may run SQLite — so a model gaining a column used to break every
    existing database at the first SELECT referencing it (2026-09-04:
    ``file_records.metadata_source`` 500'd all searches in production).

    Additive only: columns are added, never dropped or retyped.  A
    missing NOT NULL column without a server default cannot be added to
    a populated table portably, so it is logged loudly and skipped —
    that case needs a real migration.
    """
    from .models import Base

    inspector = inspect(conn)
    preparer = conn.dialect.identifier_preparer
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            if not column.nullable and column.server_default is None:
                logger.error(
                    "schema reconcile: cannot add NOT NULL column %s.%s "
                    "without a server default — write a migration",
                    table.name,
                    column.name,
                )
                continue
            ddl = (
                f"ALTER TABLE {preparer.quote(table.name)} "
                f"ADD COLUMN {preparer.quote(column.name)} "
                f"{column.type.compile(dialect=conn.dialect)}"
            )
            conn.execute(text(ddl))
            logger.warning(
                "schema reconcile: added missing column %s.%s (%s)",
                table.name,
                column.name,
                column.type,
            )


async def init_db() -> None:
    """Create missing tables and add missing columns to existing ones."""
    from .models import Base

    async with engine.begin() as conn:
        await conn.run_sync(_reconcile_missing_columns)
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created (%d tables)", len(Base.metadata.tables))
