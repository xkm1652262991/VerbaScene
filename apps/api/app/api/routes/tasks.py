from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.common import ApiResponse, ErrorResponse, PageResponse
from app.schemas.task import GenerationTaskProgressRead, GenerationTaskRead
from app.platform.tasks.repository import TaskConflictError
from app.platform.tasks.runtime import get_task_runtime
from app.services.task_service import get_generation_task, list_generation_task_progress, list_generation_tasks
from app.services.task_command_service import request_task_cancel, retry_task

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
    parent_task_id: str | None = Query(default=None),
    task_type: str | None = Query(default=None),
    resource_key: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
) -> PageResponse[GenerationTaskRead]:
    tasks, total = list_generation_tasks(
        db,
        project_id=project_id,
        status_filter=status,
        parent_task_id=parent_task_id,
        task_type=task_type,
        resource_key=resource_key,
        offset=offset,
        limit=limit,
    )
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


@router.post(
    "/{task_id}/cancel",
    response_model=ApiResponse[GenerationTaskRead],
    status_code=status.HTTP_202_ACCEPTED,
)
def cancel_generation_task_endpoint(
    task_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse[GenerationTaskRead]:
    task = get_generation_task(db, task_id)
    request_task_cancel(db, task)
    db.commit()
    db.refresh(task)
    get_task_runtime().wake()
    return ApiResponse(data=task)


@router.post(
    "/{task_id}/retry",
    response_model=ApiResponse[GenerationTaskRead],
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_generation_task_endpoint(
    task_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse[GenerationTaskRead]:
    source = get_generation_task(db, task_id)
    try:
        result = retry_task(db, source)
    except TaskConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "task_id": exc.task_id},
        ) from exc
    db.commit()
    db.refresh(result.task)
    get_task_runtime().wake()
    return ApiResponse(data=result.task)
