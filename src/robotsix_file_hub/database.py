"""Async SQLAlchemy database engine and session factory."""

import logging
from collections.abc import AsyncGenerator

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


async def init_db() -> None:
    """Create all database tables and confirm completion."""
    from .models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created (%d tables)", len(Base.metadata.tables))
