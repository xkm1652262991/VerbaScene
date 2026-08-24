from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.platform.tasks.runtime import get_task_runtime
from app.schemas.common import ApiResponse, PageResponse
from app.schemas.workflow import ProjectStageRunRead
from app.services.stage_run_service import cancel_stage_run, list_project_stage_runs_page

router = APIRouter(tags=["workflow"])


@router.get("/api/projects/{project_id}/workflow/runs", response_model=PageResponse[ProjectStageRunRead])
def list_project_stage_runs_endpoint(
    project_id: str,
    stage: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> PageResponse[ProjectStageRunRead]:
    runs, total = list_project_stage_runs_page(db, project_id, stage=stage, offset=offset, limit=limit)
    return PageResponse(items=runs, meta={"total": total, "offset": offset, "limit": limit})


@router.post("/api/workflow/runs/{run_id}/cancel", response_model=ApiResponse[ProjectStageRunRead])
def cancel_project_stage_run_endpoint(
    run_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse[ProjectStageRunRead]:
    run = cancel_stage_run(db, run_id)
    get_task_runtime().wake()
    return ApiResponse(data=run)
