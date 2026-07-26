"""File endpoints: upload, download, metadata, listing, and search."""

import contextlib
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_auth
from ..config import Settings
from ..database import get_db
from ..models import FileRecord
from ..schemas import (
    BatchUploadResponse,
    ErrorResponse,
    FileListResponse,
    FileMetadataResponse,
    FileUploadResponse,
    SearchRequest,
    SearchResponse,
)
from ..search import search_files
from ..storage import StorageBackend, StorageError, compute_checksum, create_storage_backend
from ..tasks import enqueue_enrichment, enqueue_reindex_all, get_reindex_progress

router = APIRouter(prefix="/files", tags=["files"], dependencies=[Depends(require_auth)])
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
) -> FileRecord:
    """Read, validate, store, and stage a single file upload.

    Does **not** commit the session — callers must commit (or
    rollback) and then refresh the returned record before reading
    server-generated fields such as ``created_at``.
    """
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
        storage_key = await storage.save(file_id, content)
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
        storage_key=storage_key,
    )
    db.add(record)

    return record


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
    record = await _process_upload(file, storage, db)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database failure: {exc}",
        ) from exc
    await db.refresh(record)
    task_id = enqueue_enrichment(
        file_id=record.id,
        storage_key=record.storage_key,
        content_type=record.content_type,
    )
    return FileUploadResponse(
        id=record.id,
        filename=record.filename,
        size=record.size,
        content_type=record.content_type,
        checksum=record.checksum,
        created_at=record.created_at,
        task_id=task_id,
    )


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
    """Upload multiple files in a single batch request.

    All files must succeed — if any file fails the entire batch is
    rolled back (both database records and stored file bytes).
    """
    records: list[FileRecord] = []
    for file in files:
        try:
            record = await _process_upload(file, storage, db)
        except HTTPException:
            await db.rollback()
            await _cleanup_storage(storage, records)
            raise
        except Exception as exc:
            await db.rollback()
            await _cleanup_storage(storage, records)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Upload failed: {exc}",
            ) from exc
        records.append(record)

    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        await _cleanup_storage(storage, records)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database failure: {exc}",
        ) from exc

    task_ids: dict[str, str] = {}
    for record in records:
        await db.refresh(record)
        task_ids[record.id] = enqueue_enrichment(
            file_id=record.id,
            storage_key=record.storage_key,
            content_type=record.content_type,
        )

    return BatchUploadResponse(
        files=[
            FileUploadResponse(
                id=r.id,
                filename=r.filename,
                size=r.size,
                content_type=r.content_type,
                checksum=r.checksum,
                created_at=r.created_at,
                task_id=task_ids.get(r.id),
            )
            for r in records
        ]
    )


async def _cleanup_storage(
    storage: StorageBackend,
    records: list[FileRecord],
) -> None:
    """Best-effort deletion of stored bytes for *records*."""
    for record in records:
        with contextlib.suppress(StorageError):
            await storage.delete(record.storage_key)


@router.get(
    "/{file_id}",
    responses={404: {"model": ErrorResponse}},
)
async def download_file(
    file_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    storage: Annotated[StorageBackend, Depends(_get_storage)],
) -> Response:
    """Stream the raw file bytes for a stored file."""
    record = await db.get(FileRecord, file_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    try:
        content = await storage.get(record.storage_key)
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Storage failure: {exc}",
        ) from exc

    return Response(
        content=content,
        media_type=record.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{record.filename}"',
            "Content-Length": str(record.size),
        },
    )


@router.get(
    "/{file_id}/metadata",
    response_model=FileMetadataResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_file_metadata(
    file_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FileMetadataResponse:
    """Return the full DB record for a stored file."""
    record = await db.get(FileRecord, file_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return FileMetadataResponse.model_validate(record)


@router.post(
    "/reindex",
    responses={500: {"model": ErrorResponse}},
)
async def reindex_files(
    db: Annotated[AsyncSession, Depends(get_db)],
    category: Annotated[
        str | None, Query(description="Only re-index files with this category")
    ] = None,
    content_type: Annotated[
        str | None, Query(description="Only re-index files with this MIME type")
    ] = None,
    file_ids: Annotated[
        str | None, Query(description="Comma-separated file IDs to re-index")
    ] = None,
) -> dict[str, int | str]:
    """Enqueue enrichment jobs for existing files, optionally filtered.

    Query parameters allow filtering by category, content_type, or
    a comma-separated list of specific file IDs.
    """
    parsed_file_ids: list[str] | None = None
    if file_ids is not None:
        parsed_file_ids = [fid.strip() for fid in file_ids.split(",") if fid.strip()]

    return await enqueue_reindex_all(
        category=category,
        content_type=content_type,
        file_ids=parsed_file_ids,
    )


@router.get(
    "/reindex/progress",
)
async def reindex_progress() -> dict[str, int | bool | str | None]:
    """Return the current reindex operation progress.

    Returns ``total``, ``completed``, ``failed``, and ``active``
    fields.  ``active`` is ``True`` while a reindex batch is still
    being processed.
    """
    return get_reindex_progress()


@router.post(
    "/search",
    response_model=SearchResponse,
    responses={500: {"model": ErrorResponse}},
)
async def search(
    body: Annotated[SearchRequest, Body()],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SearchResponse:
    """Hybrid NL search: keyword matching + optional vector similarity.

    Accepts a natural-language query and returns ranked, paginated
    results.  Falls back to keyword-only ranking when embeddings are
    unavailable.
    """
    return await search_files(
        db=db,
        query=body.query,
        offset=body.offset,
        limit=body.limit,
    )


@router.get(
    "",
    response_model=FileListResponse,
)
async def list_files(
    db: Annotated[AsyncSession, Depends(get_db)],
    category: Annotated[str | None, Query(description="Filter by category")] = None,
    tag: Annotated[str | None, Query(description="Filter by tag (substring match)")] = None,
    content_type: Annotated[str | None, Query(description="Filter by MIME content type")] = None,
    source: Annotated[str | None, Query(description="Filter by source/uploader")] = None,
    before: Annotated[
        datetime | None, Query(description="Filter files created before this timestamp")
    ] = None,
    after: Annotated[
        datetime | None, Query(description="Filter files created after this timestamp")
    ] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
) -> FileListResponse:
    """List files with optional filters and pagination."""
    stmt = select(FileRecord)

    if category is not None:
        stmt = stmt.where(FileRecord.category == category)
    if tag is not None:
        stmt = stmt.where(FileRecord.tags.contains(tag))
    if content_type is not None:
        stmt = stmt.where(FileRecord.content_type == content_type)
    if source is not None:
        stmt = stmt.where(FileRecord.source == source)
    if before is not None:
        stmt = stmt.where(FileRecord.created_at < before)
    if after is not None:
        stmt = stmt.where(FileRecord.created_at > after)

    # Total count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total: int = (await db.execute(count_stmt)).scalar_one()

    # Paginated results
    stmt = stmt.order_by(FileRecord.created_at.desc()).offset(offset).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()

    return FileListResponse(
        files=[FileMetadataResponse.model_validate(r) for r in rows],
        total=total,
        offset=offset,
        limit=limit,
    )
