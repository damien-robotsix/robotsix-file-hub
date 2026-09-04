"""File endpoints: upload, download, metadata, and listing."""

import contextlib
import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..models import FileRecord
from ..rate_limiter import DEFAULT_RATE_LIMIT, limiter
from ..schemas import (
    BatchUploadResponse,
    CategoriesResponse,
    ErrorResponse,
    FileListResponse,
    FileMetadataResponse,
    FileUploadResponse,
    MetadataUpdateRequest,
)
from ..storage import StorageBackend, StorageError, _get_storage, compute_checksum
from ..tasks import enqueue_enrichment, enqueue_reindex_all, get_reindex_progress

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])
MAX_FILE_SIZE = get_settings().max_file_size


async def _process_upload(
    file: UploadFile,
    storage: StorageBackend,
    db: AsyncSession,
    *,
    allow_duplicate: bool = False,
) -> tuple[FileRecord, bool]:
    """Read, validate, store, and stage a single file upload.

    Returns ``(record, is_dedup)`` where *is_dedup* is ``True`` when an
    existing record with the same checksum was reused instead of storing
    a new copy.

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

    # --- Dedup check ---
    if not allow_duplicate:
        stmt = select(FileRecord).where(FileRecord.checksum == checksum).limit(1)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            logger.info(
                "Dedup: reusing existing file %s for checksum %s",
                existing.id,
                checksum,
            )
            return existing, True

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

    return record, False


@router.post(
    "",
    response_model=FileUploadResponse,
    responses={413: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
@limiter.limit(DEFAULT_RATE_LIMIT)
async def upload_file(
    request: Request,
    file: Annotated[UploadFile, File()],
    db: Annotated[AsyncSession, Depends(get_db)],
    storage: Annotated[StorageBackend, Depends(_get_storage)],
    allow_duplicate: Annotated[bool, Query()] = False,
) -> FileUploadResponse:
    """Upload a single file.

    By default, if a file with identical content (same checksum) already
    exists, the existing record is returned instead of storing a second
    copy (``deduplicated=True``).  Pass ``?allow_duplicate=true`` to
    bypass this check and always store a new copy.
    """
    record, is_dedup = await _process_upload(
        file,
        storage,
        db,
        allow_duplicate=allow_duplicate,
    )
    if not is_dedup:
        try:
            await db.commit()
        except Exception as exc:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database failure: {exc}",
            ) from exc
    await db.refresh(record)
    task_id: str | None = None
    if not is_dedup:
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
        deduplicated=is_dedup,
    )


@router.post(
    "/batch",
    response_model=BatchUploadResponse,
    responses={413: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
@limiter.limit(DEFAULT_RATE_LIMIT)
async def upload_files_batch(
    request: Request,
    files: Annotated[list[UploadFile], File()],
    db: Annotated[AsyncSession, Depends(get_db)],
    storage: Annotated[StorageBackend, Depends(_get_storage)],
    allow_duplicate: Annotated[bool, Query()] = False,
) -> BatchUploadResponse:
    """Upload multiple files in a single batch request.

    All files must succeed — if any file fails the entire batch is
    rolled back (both database records and stored file bytes).
    Duplicate-content files are deduplicated by default (see
    ``allow_duplicate``).
    """
    records: list[FileRecord] = []
    new_records: list[FileRecord] = []
    results: list[tuple[FileRecord, bool]] = []
    for file in files:
        try:
            record, is_dedup = await _process_upload(
                file,
                storage,
                db,
                allow_duplicate=allow_duplicate,
            )
        except HTTPException:
            await db.rollback()
            await _cleanup_storage(storage, new_records)
            raise
        except Exception as exc:
            await db.rollback()
            await _cleanup_storage(storage, new_records)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Upload failed: {exc}",
            ) from exc
        records.append(record)
        results.append((record, is_dedup))
        if not is_dedup:
            new_records.append(record)

    if new_records:
        try:
            await db.commit()
        except Exception as exc:
            await db.rollback()
            await _cleanup_storage(storage, new_records)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database failure: {exc}",
            ) from exc

    task_ids: dict[str, str] = {}
    for record, is_dedup in results:
        await db.refresh(record)
        if not is_dedup:
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
                deduplicated=dedup,
            )
            for r, dedup in results
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


@router.post(
    "/reindex",
    responses={500: {"model": ErrorResponse}},
)
@limiter.limit(DEFAULT_RATE_LIMIT)
async def reindex_files(
    request: Request,
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
    enrichment_status: Annotated[
        str | None,
        Query(
            description="Filter by enrichment status. "
            "Use 'empty' to select only files that were never enriched."
        ),
    ] = None,
    force: Annotated[
        bool,
        Query(
            description=(
                "Overwrite agent/manual-curated metadata fields. "
                "By default curated records are left untouched."
            ),
        ),
    ] = False,
) -> dict[str, int | str]:
    """Enqueue enrichment jobs for existing files, optionally filtered.

    Query parameters allow filtering by category, content_type,
    a comma-separated list of specific file IDs, or enrichment_status
    (``empty`` selects files with no summary/embedding).  Unless
    ``force=true``, records whose metadata was curated by an agent or
    operator (``metadata_source`` is ``agent``/``manual``) are skipped.
    """
    parsed_file_ids: list[str] | None = None
    if file_ids is not None:
        parsed_file_ids = [fid.strip() for fid in file_ids.split(",") if fid.strip()]

    return await enqueue_reindex_all(
        category=category,
        content_type=content_type,
        file_ids=parsed_file_ids,
        enrichment_status=enrichment_status,
        force=force,
    )


@router.get(
    "/reindex/progress",
)
@limiter.limit(DEFAULT_RATE_LIMIT)
async def reindex_progress(request: Request) -> dict[str, int | bool | str | None]:
    """Return the current reindex operation progress.

    Returns ``total``, ``completed``, ``failed``, and ``active``
    fields.  ``active`` is ``True`` while a reindex batch is still
    being processed.
    """
    return get_reindex_progress()


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
