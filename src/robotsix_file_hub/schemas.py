"""Pydantic request/response schemas."""

from datetime import datetime

from pydantic import BaseModel


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


class ErrorResponse(BaseModel):
    detail: str
