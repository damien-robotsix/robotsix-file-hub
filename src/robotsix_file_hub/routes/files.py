"""File upload endpoints: POST /files and POST /files/batch."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..database import get_db
from ..models import FileRecord
from ..schemas import BatchUploadResponse, ErrorResponse, FileUploadResponse
from ..storage import StorageBackend, StorageError, compute_checksum, create_storage_backend

router = APIRouter(prefix="/files", tags=["files"])
MAX_FILE_SIZE = Settings().max_file_size

_storage: StorageBackend | None = None


def _get_storage() -> StorageBackend:
    global _storage
    if _storage is None:
        _storage = create_storage_backend()
    return _storage


async def _process_upload(
    file: UploadFile,
    storage: StorageBackend,
    db: AsyncSession,
) -> FileUploadResponse:
    # Read file content
    try:
        content = await file.read()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to read uploaded file: {exc}",
        ) from exc

    # Validate size
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File exceeds maximum size of {MAX_FILE_SIZE} bytes",
        )

    # Determine content-type
    content_type = file.content_type or "application/octet-stream"

    # Compute checksum
    checksum = compute_checksum(content)

    # Generate file ID
    file_id = str(uuid.uuid4())

    # Store file bytes
    try:
        storage_path = await storage.save(file_id, content)
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Storage failure: {exc}",
        ) from exc

    # Create DB record
    record = FileRecord(
        id=file_id,
        filename=file.filename or "unnamed",
        size=len(content),
        content_type=content_type,
        checksum=checksum,
        storage_path=storage_path,
    )
    try:
        db.add(record)
        await db.commit()
        await db.refresh(record)
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database failure: {exc}",
        ) from exc

    return FileUploadResponse.model_validate(record)


@router.post(
    "",
    response_model=FileUploadResponse,
    responses={413: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def upload_file(
    file: Annotated[UploadFile, File()],
    db: Annotated[AsyncSession, Depends(get_db)],
    storage: Annotated[StorageBackend, Depends(_get_storage)],
) -> FileUploadResponse:
    """Upload a single file."""
    return await _process_upload(file, storage, db)


@router.post(
    "/batch",
    response_model=BatchUploadResponse,
    responses={413: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def upload_files_batch(
    files: Annotated[list[UploadFile], File()],
    db: Annotated[AsyncSession, Depends(get_db)],
    storage: Annotated[StorageBackend, Depends(_get_storage)],
) -> BatchUploadResponse:
    """Upload multiple files in a single batch request."""
    results = []
    for file in files:
        result = await _process_upload(file, storage, db)
        results.append(result)
    return BatchUploadResponse(files=results)
