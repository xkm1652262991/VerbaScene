from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.common import ApiResponse, ErrorResponse, PageResponse
from app.schemas.task import GenerationTaskProgressRead, GenerationTaskRead
from app.services.script_generation_queue_service import cancel_queued_script_generation_task
from app.services.script_service import SCRIPT_GENERATION_TASK_TYPE
from app.services.task_service import get_generation_task, list_generation_task_progress, list_generation_tasks
from app.services.video_generation_queue_service import cancel_queued_video_generation_task

router = APIRouter(
    prefix="/api/tasks",
    tags=["tasks"],
    responses={
        404: {"model": ErrorResponse, "description": "Resource not found"},
        409: {"model": ErrorResponse, "description": "State conflict"},
        422: {"model": ErrorResponse, "description": "Validation error"},
    },
)


@router.get("", response_model=PageResponse[GenerationTaskRead])
def list_generation_tasks_endpoint(
    project_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
) -> PageResponse[GenerationTaskRead]:
    tasks, total = list_generation_tasks(db, project_id=project_id, status_filter=status, offset=offset, limit=limit)
    return PageResponse(items=tasks, meta={"total": total, "offset": offset, "limit": limit})


@router.get("/progress", response_model=PageResponse[GenerationTaskProgressRead])
def list_generation_task_progress_endpoint(
    project_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
) -> PageResponse[GenerationTaskProgressRead]:
    tasks, total = list_generation_task_progress(
        db,
        project_id=project_id,
        status_filter=status,
        offset=offset,
        limit=limit,
    )
    return PageResponse(items=tasks, meta={"total": total, "offset": offset, "limit": limit})


@router.get("/{task_id}", response_model=ApiResponse[GenerationTaskRead])
def get_generation_task_endpoint(
    task_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse[GenerationTaskRead]:
    return ApiResponse(data=get_generation_task(db, task_id))


@router.delete("/{task_id}/queue", response_model=ApiResponse[GenerationTaskRead])
def cancel_queued_generation_task_endpoint(
    task_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse[GenerationTaskRead]:
    task = get_generation_task(db, task_id)
    if task.task_type == SCRIPT_GENERATION_TASK_TYPE:
        return ApiResponse(data=cancel_queued_script_generation_task(db, task_id))
    return ApiResponse(data=cancel_queued_video_generation_task(db, task_id))
