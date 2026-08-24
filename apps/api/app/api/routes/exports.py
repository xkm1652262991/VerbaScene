from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.common import ApiResponse, PageResponse
from app.schemas.export import CompositionRequest, CompositionResponse, ExportRead
from app.services.export_service import compose_project, list_exports_page

router = APIRouter(tags=["exports"])


@router.post("/api/projects/{project_id}/compose", response_model=ApiResponse[CompositionResponse])
def compose_project_endpoint(
    project_id: str,
    payload: CompositionRequest | None = None,
    db: Session = Depends(get_db),
) -> ApiResponse[CompositionResponse]:
    export, asset = compose_project(
        db,
        project_id,
        subtitle_mode=payload.subtitle_mode if payload else "none",
    )
    return ApiResponse(data={"export": export, "asset": asset})


@router.get("/api/projects/{project_id}/exports", response_model=PageResponse[ExportRead])
def list_project_exports_endpoint(
    project_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
) -> PageResponse[ExportRead]:
    exports, total = list_exports_page(db, project_id, offset=offset, limit=limit)
    return PageResponse(items=exports, meta={"total": total, "offset": offset, "limit": limit})
