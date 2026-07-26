"""Pydantic request/response schemas."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


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

    model_config = {"from_attributes": True}


class BatchUploadResponse(BaseModel):
    files: list[FileUploadResponse]


class FileMetadataResponse(BaseModel):
    """Full file metadata including optional enrichment fields."""

    id: str
    filename: str
    size: int
    content_type: str
    checksum: str
    storage_key: str
    created_at: datetime
    updated_at: datetime
    category: str | None = None
    tags: str | None = None
    summary: str | None = None
    source: str | None = None

    model_config = {"from_attributes": True}


class FileListResponse(BaseModel):
    files: list[FileMetadataResponse]
    total: int
    offset: int
    limit: int


class ErrorResponse(BaseModel):
    detail: str


class SearchRequest(BaseModel):
    query: str
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=1000)


class SearchResult(BaseModel):
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
    relevance: float

    model_config = {"from_attributes": True}


class SearchResponse(BaseModel):
    results: list[SearchResult]
    total: int
    offset: int
    limit: int
    query: str
