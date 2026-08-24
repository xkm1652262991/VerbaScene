from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.common import PageResponse
from app.schemas.shot_frame import ShotFrameImageRead
from app.services.shot_frame_service import list_shot_frame_images_page

router = APIRouter(tags=["shot-frames"])


@router.get("/api/projects/{project_id}/shot-frame-images", response_model=PageResponse[ShotFrameImageRead])
def list_project_shot_frame_images_endpoint(
    project_id: str,
    shot_id: str | None = Query(default=None),
    frame_type: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> PageResponse[ShotFrameImageRead]:
    images, total = list_shot_frame_images_page(
        db,
        project_id,
        shot_id=shot_id,
        frame_type=frame_type,
        offset=offset,
        limit=limit,
    )
    return PageResponse(items=images, meta={"total": total, "offset": offset, "limit": limit})
