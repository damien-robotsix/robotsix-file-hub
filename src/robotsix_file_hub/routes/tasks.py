"""Task status endpoint for background job tracking."""

import logging

from fastapi import APIRouter, HTTPException, Request, status

from ..rate_limiter import DEFAULT_RATE_LIMIT, limiter
from ..schemas import ErrorResponse, TaskResponse
from ..tasks import get_task

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
