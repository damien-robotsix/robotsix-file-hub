"""Task status endpoint for background job tracking."""

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_current_user
from ..schemas import ErrorResponse, TaskResponse
from ..tasks import get_task

router = APIRouter(prefix="/tasks", tags=["tasks"], dependencies=[Depends(get_current_user)])


@router.get(
    "/{task_id}",
    response_model=TaskResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_task_status(task_id: str) -> TaskResponse:
    """Return the current status of a background task.

    Returns the task type, status, progress (for reindex tasks),
    and any error message if the task failed.
    """
    task = get_task(task_id)
    if task is None:
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
