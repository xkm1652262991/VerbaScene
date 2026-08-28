from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.common import ApiResponse, PageResponse
from app.schemas.shot import (
    ShotCreate,
    ShotPromptPreviewRead,
    ShotRead,
    ShotReferenceAssetsUpdate,
    ShotReorderRequest,
    ShotUpdate,
    ShotVideoReferenceUpdate,
    ShotVideoPromptPreviewRead,
)
from app.schemas.task import GenerationTaskRead
from app.platform.tasks.runtime import get_task_runtime
from app.production.shot_direction.service import create_shot_direction_task
from app.services.shot_service import (
    compile_shot_video_prompt,
    create_shot,
    delete_shot,
    list_shots,
    list_shots_page,
    reorder_shots,
    preview_shot_video_prompt,
    update_shot,
    update_shot_reference_assets,
    update_shot_video_reference,
)
from app.services.pre_image_service import build_project_prompt_previews

router = APIRouter(tags=["shots"])


@router.post(
    "/api/projects/{project_id}/shots/generate",
    response_model=ApiResponse[GenerationTaskRead],
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_project_shots_endpoint(
    project_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> ApiResponse[GenerationTaskRead]:
    task = create_shot_direction_task(db, project_id, idempotency_key=idempotency_key)
    get_task_runtime().wake()
    return ApiResponse(data=task)


@router.get("/api/projects/{project_id}/shots", response_model=PageResponse[ShotRead])
def list_project_shots_endpoint(
    project_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> PageResponse[ShotRead]:
    shots, total = list_shots_page(db, project_id, offset=offset, limit=limit)
    return PageResponse(items=shots, meta={"total": total, "offset": offset, "limit": limit})


@router.post("/api/projects/{project_id}/shots", response_model=ApiResponse[ShotRead], status_code=status.HTTP_201_CREATED)
def create_project_shot_endpoint(
    project_id: str,
    payload: ShotCreate,
    db: Session = Depends(get_db),
) -> ApiResponse[ShotRead]:
    return ApiResponse(data=create_shot(db, project_id, payload))


@router.patch("/api/shots/{shot_id}", response_model=ApiResponse[ShotRead])
def update_shot_endpoint(
    shot_id: str,
    payload: ShotUpdate,
    db: Session = Depends(get_db),
) -> ApiResponse[ShotRead]:
    return ApiResponse(data=update_shot(db, shot_id, payload))


@router.patch("/api/shots/{shot_id}/video-reference", response_model=ApiResponse[ShotRead])
def update_shot_video_reference_endpoint(
    shot_id: str,
    payload: ShotVideoReferenceUpdate,
    db: Session = Depends(get_db),
) -> ApiResponse[ShotRead]:
    return ApiResponse(data=update_shot_video_reference(db, shot_id, payload.asset_id))


@router.put("/api/shots/{shot_id}/reference-assets", response_model=ApiResponse[ShotRead])
def update_shot_reference_assets_endpoint(
    shot_id: str,
    payload: ShotReferenceAssetsUpdate,
    db: Session = Depends(get_db),
) -> ApiResponse[ShotRead]:
    return ApiResponse(data=update_shot_reference_assets(db, shot_id, payload.asset_ids))


@router.get(
    "/api/shots/{shot_id}/video-prompt-preview",
    response_model=ApiResponse[ShotVideoPromptPreviewRead],
)
def preview_shot_video_prompt_endpoint(
    shot_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse[ShotVideoPromptPreviewRead]:
    return ApiResponse(data=preview_shot_video_prompt(db, shot_id))


@router.post(
    "/api/shots/{shot_id}/compile-video-prompt",
    response_model=ApiResponse[ShotRead],
)
def compile_shot_video_prompt_endpoint(
    shot_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse[ShotRead]:
    return ApiResponse(data=compile_shot_video_prompt(db, shot_id))


@router.delete("/api/shots/{shot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shot_endpoint(
    shot_id: str,
    db: Session = Depends(get_db),
) -> Response:
    delete_shot(db, shot_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/shots/reorder", response_model=ApiResponse[list[ShotRead]])
def reorder_shots_endpoint(
    payload: ShotReorderRequest,
    db: Session = Depends(get_db),
) -> ApiResponse[list[ShotRead]]:
    return ApiResponse(data=reorder_shots(db, payload))


@router.get("/api/projects/{project_id}/shots/prompt-preview", response_model=PageResponse[ShotPromptPreviewRead])
def get_project_shot_prompt_preview_endpoint(
    project_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> PageResponse[ShotPromptPreviewRead]:
    previews = build_project_prompt_previews(db, project_id)
    return PageResponse(items=previews[offset : offset + limit], meta={"total": len(previews), "offset": offset, "limit": limit})
