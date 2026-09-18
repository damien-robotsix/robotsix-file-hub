"""File endpoints: list, download, inline view, metadata, and deletion."""

import logging
from datetime import datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import FileRecord
from ..rate_limiter import DEFAULT_RATE_LIMIT, limiter
from ..schemas import (
    CategoriesResponse,
    ErrorResponse,
    FileListResponse,
    FileMetadataResponse,
    MetadataUpdateRequest,
)
from ..storage import StorageBackend, StorageError, _get_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])


@router.get(
    "/categories",
    response_model=CategoriesResponse,
)
@limiter.limit(DEFAULT_RATE_LIMIT)
async def list_categories(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CategoriesResponse:
    """Return a sorted list of distinct categories across all files."""
    stmt = select(FileRecord.category).where(FileRecord.category.isnot(None)).distinct()
    rows = (await db.execute(stmt)).scalars().all()
    return CategoriesResponse(categories=sorted(c for c in rows if c is not None))


async def _stream_file(
    file_id: str,
    disposition: str,
    db: AsyncSession,
    storage: StorageBackend,
) -> Response:
    """Build a streaming response for a stored file.

    Resolves the file record (404 when missing), reads its bytes from
    storage (500 on ``StorageError``), and constructs the response with
    the given ``Content-Disposition`` disposition word — ``"attachment"``
    for downloads, ``"inline"`` for in-browser rendering.
    """
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
            "Content-Disposition": f'{disposition}; filename="{record.filename}"',
            "Content-Length": str(record.size),
        },
    )


@router.get(
    "/{file_id}",
    responses={404: {"model": ErrorResponse}},
)
@limiter.limit(DEFAULT_RATE_LIMIT)
async def download_file(
    request: Request,
    file_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    storage: Annotated[StorageBackend, Depends(_get_storage)],
) -> Response:
    """Stream the raw file bytes for a stored file."""
    return await _stream_file(file_id, "attachment", db, storage)


@router.get(
    "/{file_id}/view",
    responses={404: {"model": ErrorResponse}},
)
@limiter.limit(DEFAULT_RATE_LIMIT)
async def view_file(
    request: Request,
    file_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    storage: Annotated[StorageBackend, Depends(_get_storage)],
) -> Response:
    """Serve the file with inline disposition for browser rendering.

    Unlike the download endpoint, this sets ``Content-Disposition: inline``
    so that browsers (and headless-browser render tools) can display the
    file content directly — PDFs render in-page, images show inline, etc.
    """
    return await _stream_file(file_id, "inline", db, storage)


@router.delete(
    "/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
)
@limiter.limit(DEFAULT_RATE_LIMIT)
async def delete_file(
    request: Request,
    file_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    storage: Annotated[StorageBackend, Depends(_get_storage)],
    x_confirm_delete: Annotated[str | None, Header()] = None,
    confirm: Annotated[str | None, Query()] = None,
) -> None:
    """Delete a stored file and its database record.

    Requires a confirmation guard to prevent accidental deletion.
    Pass either the ``X-Confirm-Delete: true`` header or the
    ``?confirm=true`` query parameter.
    """
    record = await db.get(FileRecord, file_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    if x_confirm_delete != "true" and confirm != "true":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Confirmation required: set X-Confirm-Delete: true header "
                "or ?confirm=true query parameter"
            ),
        )

    # Delete the DB record first so we never have an orphan row
    # pointing to already-deleted storage bytes.
    await db.delete(record)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database failure: {exc}",
        ) from exc

    # Best-effort storage cleanup — the record is already gone, so
    # a stale file on disk is harmless; log and move on.
    try:
        await storage.delete(record.storage_key)
    except StorageError:
        logger.warning("Failed to delete storage key %s for file %s", record.storage_key, file_id)


@router.get(
    "/{file_id}/metadata",
    response_model=FileMetadataResponse,
    responses={404: {"model": ErrorResponse}},
)
@limiter.limit(DEFAULT_RATE_LIMIT)
async def get_file_metadata(
    request: Request,
    file_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FileMetadataResponse:
    """Return the full DB record for a stored file."""
    record = await db.get(FileRecord, file_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return FileMetadataResponse.model_validate(record)


@router.patch(
    "/{file_id}/metadata",
    response_model=FileMetadataResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
@limiter.limit(DEFAULT_RATE_LIMIT)
async def update_file_metadata(
    request: Request,
    file_id: str,
    body: MetadataUpdateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FileMetadataResponse:
    """Set or overwrite a single file's curated enrichment metadata.

    Accepts a partial body — any subset of ``summary``/``category``/
    ``tags``.  Omitted fields are left unchanged; an explicit ``null``
    clears the field.  The record's ``metadata_source`` marker records
    the provenance of the updated values (default ``"manual"``, or
    ``"agent"`` when the caller passes it), so later automatic
    enrichment/reindex passes will not clobber the curated values
    unless explicitly forced.
    """
    record = await db.get(FileRecord, file_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    provided = body.model_fields_set
    data_fields = provided & {"summary", "category", "tags"}
    if not data_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one of summary, category, or tags",
        )

    if "summary" in data_fields:
        record.summary = body.summary
    if "category" in data_fields:
        record.category = body.category
    if "tags" in data_fields:
        record.tags = ",".join(body.tags) if body.tags else None

    record.metadata_source = body.metadata_source or "manual"

    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database failure: {exc}",
        ) from exc
    await db.refresh(record)
    return FileMetadataResponse.model_validate(record)


@router.get(
    "",
    response_model=FileListResponse,
)
@limiter.limit(DEFAULT_RATE_LIMIT)
async def list_files(
    request: Request,
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
