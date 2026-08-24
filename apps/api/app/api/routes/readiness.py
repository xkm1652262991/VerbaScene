from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.common import ApiResponse
from app.schemas.readiness import ProjectReadinessRead
from app.services.readiness_service import get_project_readiness

router = APIRouter(tags=["readiness"])


@router.get("/api/projects/{project_id}/readiness", response_model=ApiResponse[ProjectReadinessRead])
def get_project_readiness_endpoint(
    project_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse[ProjectReadinessRead]:
    return ApiResponse(data=get_project_readiness(db, project_id))
