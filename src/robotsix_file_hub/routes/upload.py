"""File upload endpoints: single-file and batch upload."""

import contextlib
import hashlib
import json
import logging
import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_db
from ..models import FileRecord
from ..rate_limiter import DEFAULT_RATE_LIMIT, limiter
from ..schemas import (
    BatchUploadResponse,
    ErrorResponse,
    FileUploadResponse,
    UploadMetadata,
)
from ..storage import STREAM_CHUNK_SIZE, StorageBackend, StorageError, _get_storage
from ..tasks import enqueue_enrichment
from .errors import internal_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])
MAX_FILE_SIZE = get_settings().max_file_size


def _parse_upload_metadata(raw: str | None) -> UploadMetadata | None:
    """Parse the optional ``metadata`` upload form field.

    Accepts a JSON object string carrying the caller-supplied
    ``context``/``tags``/``provenance`` payload.  Returns ``None`` when
    the field is absent or blank (fully backward-compatible), and raises
    ``400`` when the payload is not valid JSON matching
    :class:`UploadMetadata`.
    """
    if raw is None or raw.strip() == "":
        return None
    try:
        return UploadMetadata.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid metadata payload: {exc}",
        ) from exc


def _parse_batch_metadata(raw: str | None, count: int) -> list[UploadMetadata | None]:
    """Parse the optional batch ``metadata`` form field.

    Accepts a JSON array of per-file metadata objects (``null`` entries
    allowed), index-aligned with the uploaded ``files``.  A shorter array
    is padded with ``None``; an absent field yields an all-``None`` list.
    Raises ``400`` when the payload is not a JSON array, has more entries
    than files, or an entry does not match :class:`UploadMetadata`.
    """
    if raw is None or raw.strip() == "":
        return [None] * count
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid metadata payload: {exc}",
        ) from exc
    if not isinstance(decoded, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Batch metadata must be a JSON array aligned with files",
        )
    if len(decoded) > count:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Batch metadata has {len(decoded)} entries but only {count} files were uploaded"
            ),
        )
    parsed: list[UploadMetadata | None] = []
    for entry in decoded:
        if entry is None:
            parsed.append(None)
            continue
        try:
            parsed.append(UploadMetadata.model_validate(entry))
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid metadata payload: {exc}",
            ) from exc
    parsed.extend([None] * (count - len(parsed)))
    return parsed


async def _process_upload(
    file: UploadFile,
    storage: StorageBackend,
    db: AsyncSession,
    *,
    allow_duplicate: bool = False,
    metadata: UploadMetadata | None = None,
) -> tuple[FileRecord, bool]:
    """Read, validate, store, and stage a single file upload.

    Returns ``(record, is_dedup)`` where *is_dedup* is ``True`` when an
    existing record with the same checksum was reused instead of storing
    a new copy.

    Does **not** commit the session — callers must commit (or
    rollback) and then refresh the returned record before reading
    server-generated fields such as ``created_at``.
    """
    # Read file content in bounded chunks, enforcing the size limit and
    # computing the checksum incrementally so the full payload is never
    # materialised in memory.
    hasher = hashlib.sha256()
    size = 0
    try:
        while chunk := await file.read(STREAM_CHUNK_SIZE):
            size += len(chunk)
            if size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=f"File exceeds maximum size of {MAX_FILE_SIZE} bytes",
                )
            hasher.update(chunk)
        await file.seek(0)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to read uploaded file: {exc}",
        ) from exc

    # Determine content-type
    content_type = file.content_type or "application/octet-stream"

    checksum = hasher.hexdigest()

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
        storage_key = await storage.save_stream(file_id, file.file)
    except StorageError as exc:
        raise internal_error("Storage failure", exc) from exc

    # Create DB record
    record = FileRecord(
        id=file_id,
        filename=file.filename or "unnamed",
        size=size,
        content_type=content_type,
        checksum=checksum,
        storage_key=storage_key,
        upload_metadata=(
            metadata.model_dump_json(exclude_none=True, by_alias=True)
            if metadata is not None
            else None
        ),
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
    metadata: Annotated[str | None, Form()] = None,
) -> FileUploadResponse:
    """Upload a single file.

    By default, if a file with identical content (same checksum) already
    exists, the existing record is returned instead of storing a second
    copy (``deduplicated=True``).  Pass ``?allow_duplicate=true`` to
    bypass this check and always store a new copy.

    The optional ``metadata`` form field carries a JSON object with
    caller-supplied provenance/context (``context``, ``tags``,
    ``provenance``); it is persisted with the file and fed into the
    enrichment classifier.  Omitting it is fully backward-compatible.
    """
    parsed_metadata = _parse_upload_metadata(metadata)
    record, is_dedup = await _process_upload(
        file,
        storage,
        db,
        allow_duplicate=allow_duplicate,
        metadata=parsed_metadata,
    )
    if not is_dedup:
        await _commit_or_cleanup(db, storage, [record])
    await db.refresh(record)
    task_id = _enqueue_if_new(record, is_dedup)
    return _to_upload_response(record, is_dedup, task_id)


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
    metadata: Annotated[str | None, Form()] = None,
) -> BatchUploadResponse:
    """Upload multiple files in a single batch request.

    All files must succeed — if any file fails the entire batch is
    rolled back (both database records and stored file bytes).
    Duplicate-content files are deduplicated by default (see
    ``allow_duplicate``).

    The optional ``metadata`` form field carries a JSON array of
    per-file provenance/context objects (``null`` entries allowed),
    index-aligned with ``files``; each is persisted with its file and
    fed into the enrichment classifier.  Omitting it is fully
    backward-compatible.
    """
    per_file_metadata = _parse_batch_metadata(metadata, len(files))
    records: list[FileRecord] = []
    new_records: list[FileRecord] = []
    results: list[tuple[FileRecord, bool]] = []
    for file, file_metadata in zip(files, per_file_metadata, strict=True):
        try:
            record, is_dedup = await _process_upload(
                file,
                storage,
                db,
                allow_duplicate=allow_duplicate,
                metadata=file_metadata,
            )
        except HTTPException:
            await db.rollback()
            await _cleanup_storage(storage, new_records)
            raise
        except Exception as exc:
            await db.rollback()
            await _cleanup_storage(storage, new_records)
            raise internal_error("Upload failed", exc) from exc
        records.append(record)
        results.append((record, is_dedup))
        if not is_dedup:
            new_records.append(record)

    if new_records:
        await _commit_or_cleanup(db, storage, new_records)

    task_ids: dict[str, str | None] = {}
    for record, is_dedup in results:
        await db.refresh(record)
        if not is_dedup:
            task_ids[record.id] = _enqueue_if_new(record, is_dedup)

    return BatchUploadResponse(
        files=[_to_upload_response(r, dedup, task_ids.get(r.id)) for r, dedup in results]
    )


async def _commit_or_cleanup(
    db: AsyncSession,
    storage: StorageBackend,
    new_records: list[FileRecord],
) -> None:
    """Commit the session; on failure roll back and delete stored bytes.

    Raises ``500`` when the commit fails, after rolling back the session
    and best-effort deleting the blobs of *new_records* so no orphaned
    bytes are left in storage without a matching DB row.
    """
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        await _cleanup_storage(storage, new_records)
        raise internal_error("Database failure", exc) from exc


def _enqueue_if_new(record: FileRecord, is_dedup: bool) -> str | None:
    """Enqueue enrichment for a newly stored *record*; skip dedup hits."""
    if is_dedup:
        return None
    return enqueue_enrichment(
        file_id=record.id,
        storage_key=record.storage_key,
        content_type=record.content_type,
        upload_metadata=record.upload_metadata,
    )


def _to_upload_response(
    record: FileRecord,
    dedup: bool,
    task_id: str | None,
) -> FileUploadResponse:
    """Build the API response for an uploaded (or deduplicated) record."""
    return FileUploadResponse(
        id=record.id,
        filename=record.filename,
        size=record.size,
        content_type=record.content_type,
        checksum=record.checksum,
        created_at=record.created_at,
        task_id=task_id,
        deduplicated=dedup,
    )


async def _cleanup_storage(
    storage: StorageBackend,
    records: list[FileRecord],
) -> None:
    """Best-effort deletion of stored bytes for *records*."""
    for record in records:
        with contextlib.suppress(StorageError):
            await storage.delete(record.storage_key)
