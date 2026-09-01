"""Pydantic request/response schemas."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TaskType(StrEnum):
    enrichment = "enrichment"
    reindex = "reindex"


class TaskStatus(StrEnum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class TaskResponse(BaseModel):
    """Status of a background task (enrichment or reindex)."""

    task_id: str
    type: TaskType
    status: TaskStatus
    file_id: str | None = None
    progress: int | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class FileUploadResponse(BaseModel):
    id: str
    filename: str
    size: int
    content_type: str
    checksum: str
    created_at: datetime
    task_id: str | None = None
    deduplicated: bool = False

    model_config = {"from_attributes": True}


class BatchUploadResponse(BaseModel):
    files: list[FileUploadResponse]


class _FileMetadataBase(BaseModel):
    """Shared base for file metadata responses and search results."""

    id: str
    filename: str
    size: int
    content_type: str
    checksum: str
    created_at: datetime
    category: str | None = None
    tags: str | None = None
    summary: str | None = None
    source: str | None = None
    metadata_source: str | None = None

    model_config = {"from_attributes": True}


class FileMetadataResponse(_FileMetadataBase):
    """Full file metadata including optional enrichment fields."""

    storage_key: str
    updated_at: datetime


class FileListResponse(BaseModel):
    files: list[FileMetadataResponse]
    total: int
    offset: int
    limit: int


class CategoriesResponse(BaseModel):
    categories: list[str]


class ErrorResponse(BaseModel):
    detail: str


class MetadataUpdateRequest(BaseModel):
    """Partial enrichment-metadata update body.

    Accepts any subset of ``summary``/``category``/``tags``; omitted
    fields are left unchanged and an explicit ``null`` clears a field.
    ``tags`` is an ordered list of up to 10 keyword strings, stored
    comma-separated like the enrichment pipeline writes them.
    ``metadata_source`` records the provenance of the curated values
    (``"agent"`` or ``"manual"``; defaults to ``"manual"``).
    """

    summary: str | None = None
    category: str | None = None
    tags: list[str] | None = Field(
        default=None,
        max_length=10,
        description="Ordered keyword tags (max 10), stored comma-separated.",
    )
    metadata_source: Literal["agent", "manual"] | None = Field(
        default=None,
        description='Provenance of the curated values: "agent" or "manual" (default "manual").',
    )

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, tags: list[str] | None) -> list[str] | None:
        """Strip whitespace, reject empty tags, and cap the joined length."""
        if tags is None:
            return tags
        cleaned = [tag.strip() for tag in tags]
        if any(tag == "" for tag in cleaned):
            raise ValueError("tags must not contain empty strings")
        # The DB column is VARCHAR(1024) storing comma-separated tags.
        if sum(len(tag) for tag in cleaned) + len(cleaned) - 1 > 1024:
            raise ValueError("tags too long (combined length exceeds 1024 characters)")
        return cleaned


class SearchRequest(BaseModel):
    query: str
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=1000)
    category: str | None = None
    tags: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None


class SearchResult(_FileMetadataBase):
    relevance: float = 0.0


class SearchResponse(BaseModel):
    results: list[SearchResult]
    total: int
    offset: int
    limit: int
    query: str
