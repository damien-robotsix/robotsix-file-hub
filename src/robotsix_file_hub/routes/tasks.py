"""Task status and reindex endpoints for background job tracking."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..rate_limiter import DEFAULT_RATE_LIMIT, limiter
from ..schemas import ErrorResponse, TaskResponse
from ..tasks import enqueue_reindex_all, get_reindex_progress, get_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get(
    "/{task_id}",
    response_model=TaskResponse,
    responses={404: {"model": ErrorResponse}},
)
@limiter.limit(DEFAULT_RATE_LIMIT)
async def get_task_status(request: Request, task_id: str) -> TaskResponse:
    """Return the current status of a background task.

    Returns the task type, status, progress (for reindex tasks),
    and any error message if the task failed.
    """
    logger.debug("GET /tasks/%s: retrieving task status", task_id)
    task = get_task(task_id)
    if task is None:
        logger.warning("GET /tasks/%s: task not found", task_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    return TaskResponse(
        task_id=task.task_id,
        type=task.type,
        status=task.status,
        file_id=task.file_id,
        progress=task.progress,
        error=task.error,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


reindex_router = APIRouter(prefix="/files", tags=["files"])


@reindex_router.post(
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


@reindex_router.get(
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
