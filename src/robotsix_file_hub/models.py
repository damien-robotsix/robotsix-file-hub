"""SQLAlchemy ORM models."""

import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Width of the stored embedding vector. Must match the model named by
# ``embedding.model``: bge-m3 emits 1024 dimensions, the
# retired local all-MiniLM-L6-v2 emitted 384. Changing the model without
# a matching migration makes every insert fail on a dimension mismatch,
# so this constant and migration 0003 move together.
EMBEDDING_DIMENSIONS = 1024


class Base(DeclarativeBase):
    pass


class FileRecord(Base):
    """Central persistence entity for uploaded files.

    Maps to the ``file_records`` table with 13 columns including metadata,
    enrichment fields (category, tags, summary, source, embedding), and
    timestamps.  The ``embedding`` column dimension is coupled to
    ``EMBEDDING_DIMENSIONS``; enrichment fields are nullable until the
    enrichment task runs.
    """

    __tablename__ = "file_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str] = mapped_column(String(256), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    category: Mapped[str | None] = mapped_column(String(256), nullable=True)
    tags: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    summary: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    source: Mapped[str | None] = mapped_column(String(256), nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
