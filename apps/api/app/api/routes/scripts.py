from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.common import ApiResponse
from app.schemas.script import ScriptRead, ScriptUpdate
from app.schemas.task import GenerationTaskRead
from app.services.script_generation_queue_service import schedule_script_generation_task
from app.services.script_service import (
    create_script_generation_task,
    get_latest_script,
    update_script,
)

router = APIRouter(tags=["scripts"])


@router.post(
    "/api/projects/{project_id}/script/generate",
    response_model=ApiResponse[GenerationTaskRead],
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_project_script_endpoint(
    project_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse[GenerationTaskRead]:
    task = create_script_generation_task(db, project_id)
    schedule_script_generation_task(task.id)
    return ApiResponse(data=task)


@router.get("/api/projects/{project_id}/script", response_model=ApiResponse[ScriptRead])
def get_project_script_endpoint(
    project_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse[ScriptRead]:
    script = get_latest_script(db, project_id)
    if script is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Script not found")
    return ApiResponse(data=script)


@router.patch("/api/scripts/{script_id}", response_model=ApiResponse[ScriptRead])
def update_script_endpoint(
    script_id: str,
    payload: ScriptUpdate,
    db: Session = Depends(get_db),
) -> ApiResponse[ScriptRead]:
    return ApiResponse(data=update_script(db, script_id, payload))
