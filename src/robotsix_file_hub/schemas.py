"""Pydantic request/response schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class FileUploadResponse(BaseModel):
    id: str
    filename: str
    size: int
    content_type: str
    checksum: str
    created_at: datetime

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
    storage_path: str
    created_at: datetime
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
