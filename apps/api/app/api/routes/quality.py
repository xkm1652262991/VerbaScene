from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.common import ApiResponse, PageResponse
from app.schemas.quality import QualityCheckRead, QualityCheckRunResponse
from app.services.quality_service import list_quality_checks_page, run_quality_checks

router = APIRouter(tags=["quality"])


@router.get("/api/projects/{project_id}/quality-checks", response_model=PageResponse[QualityCheckRead])
def list_project_quality_checks_endpoint(
    project_id: str,
    stage: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> PageResponse[QualityCheckRead]:
    checks, total = list_quality_checks_page(db, project_id, stage=stage, offset=offset, limit=limit)
    return PageResponse(items=checks, meta={"total": total, "offset": offset, "limit": limit})


@router.post("/api/projects/{project_id}/quality-checks/run", response_model=ApiResponse[QualityCheckRunResponse])
def run_project_quality_checks_endpoint(
    project_id: str,
    stage: str = Query(default="shots"),
    db: Session = Depends(get_db),
) -> ApiResponse[QualityCheckRunResponse]:
    checks = run_quality_checks(db, project_id, stage=stage)
    return ApiResponse(data={"checks": checks})
