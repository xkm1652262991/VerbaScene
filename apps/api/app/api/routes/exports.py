from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.exports.export_tasks import create_project_export_task
from app.platform.tasks.runtime import get_task_runtime
from app.schemas.common import ApiResponse, PageResponse
from app.schemas.export import CompositionRequest, ExportRead
from app.schemas.task import GenerationTaskRead
from app.services.export_service import list_exports_page

router = APIRouter(tags=["exports"])


@router.post(
    "/api/projects/{project_id}/compose",
    response_model=ApiResponse[GenerationTaskRead],
    status_code=status.HTTP_202_ACCEPTED,
)
def compose_project_endpoint(
    project_id: str,
    payload: CompositionRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> ApiResponse[GenerationTaskRead]:
    task = create_project_export_task(
        db,
        project_id,
        subtitle_mode=payload.subtitle_mode if payload else "none",
        idempotency_key=idempotency_key,
    )
    get_task_runtime().wake()
    return ApiResponse(data=task)


@router.get("/api/projects/{project_id}/exports", response_model=PageResponse[ExportRead])
def list_project_exports_endpoint(
    project_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> PageResponse[ExportRead]:
    exports, total = list_exports_page(db, project_id, offset=offset, limit=limit)
    return PageResponse(items=exports, meta={"total": total, "offset": offset, "limit": limit})
