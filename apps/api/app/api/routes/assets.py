from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.platform.tasks.runtime import get_task_runtime
from app.production.video_tasks import (
    create_project_video_batch_task,
    create_video_candidate_task,
    create_video_regeneration_task,
)
from app.schemas.common import ApiResponse, PageResponse
from app.schemas.asset import AssetCandidateGenerationResponse, AssetRead, ShotVideoGenerationRequest
from app.schemas.task import GenerationTaskRead
from app.services.asset_lifecycle_service import (
    delete_asset,
    extract_video_frame_candidate,
    select_asset,
    upload_image_asset,
)
from app.services.asset_repository import get_project_or_404, list_assets_page
from app.services.image_generation_service import (
    generate_reference_image_candidates,
    generate_shot_image_candidates,
    generate_single_reference_image_candidate,
    generate_single_shot_image_candidate,
    regenerate_image_asset_candidate,
)

router = APIRouter(tags=["assets"])


@router.get("/api/projects/{project_id}/assets", response_model=PageResponse[AssetRead])
def list_project_assets_endpoint(
    project_id: str,
    asset_type: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> PageResponse[AssetRead]:
    get_project_or_404(db, project_id)
    assets, total = list_assets_page(
        db,
        project_id,
        asset_type=asset_type,
        entity_type=entity_type,
        offset=offset,
        limit=limit,
    )
    return PageResponse(items=assets, meta={"total": total, "offset": offset, "limit": limit})


@router.post("/api/projects/{project_id}/reference-images/generate", deprecated=True)
def generate_project_reference_images_endpoint(
    project_id: str,
    db: Session = Depends(get_db),
) -> None:
    _ = (project_id, db)
    _raise_candidate_required("/api/projects/{project_id}/reference-images/generate-candidates")


@router.post("/api/projects/{project_id}/reference-images/generate-candidates", response_model=ApiResponse[AssetCandidateGenerationResponse])
def generate_project_reference_image_candidates_endpoint(
    project_id: str,
    image_provider_profile_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ApiResponse[AssetCandidateGenerationResponse]:
    candidates, task = generate_reference_image_candidates(
        db,
        project_id,
        image_provider_profile_id=image_provider_profile_id,
    )
    return ApiResponse(data={"candidates": candidates, "task_id": task.id})


@router.post("/api/projects/{project_id}/reference-images/generate-candidate", response_model=ApiResponse[AssetCandidateGenerationResponse])
def generate_single_reference_image_candidate_endpoint(
    project_id: str,
    entity_type: str = Query(...),
    entity_id: str = Query(...),
    asset_role: str = Query(...),
    variant_key: str = Query(default="base", min_length=1, max_length=120),
    image_provider_profile_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ApiResponse[AssetCandidateGenerationResponse]:
    candidate, task = generate_single_reference_image_candidate(
        db,
        project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        asset_role=asset_role,
        variant_key=variant_key,
        image_provider_profile_id=image_provider_profile_id,
    )
    return ApiResponse(data={"candidates": [candidate], "task_id": task.id})


@router.post("/api/projects/{project_id}/shot-images/generate", deprecated=True)
def generate_project_shot_images_endpoint(
    project_id: str,
    db: Session = Depends(get_db),
) -> None:
    _ = (project_id, db)
    _raise_candidate_required("/api/projects/{project_id}/shot-images/generate-candidates")


@router.post("/api/projects/{project_id}/shot-images/generate-candidates", response_model=ApiResponse[AssetCandidateGenerationResponse])
def generate_project_shot_image_candidates_endpoint(
    project_id: str,
    image_provider_profile_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ApiResponse[AssetCandidateGenerationResponse]:
    candidates, task = generate_shot_image_candidates(
        db,
        project_id,
        image_provider_profile_id=image_provider_profile_id,
    )
    return ApiResponse(data={"candidates": candidates, "task_id": task.id})


@router.post("/api/projects/{project_id}/shot-image-grid/generate", deprecated=True)
def generate_project_shot_image_grid_endpoint(
    project_id: str,
    rows: int = Query(default=2, ge=1, le=3),
    cols: int = Query(default=2, ge=1, le=4),
    db: Session = Depends(get_db),
) -> None:
    _ = (project_id, rows, cols, db)
    _raise_candidate_required("/api/projects/{project_id}/shot-images/generate-candidates")


@router.post("/api/shots/{shot_id}/image/generate-candidate", response_model=ApiResponse[AssetCandidateGenerationResponse])
def generate_single_shot_image_candidate_endpoint(
    shot_id: str,
    image_provider_profile_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ApiResponse[AssetCandidateGenerationResponse]:
    candidate, task = generate_single_shot_image_candidate(
        db,
        shot_id,
        image_provider_profile_id=image_provider_profile_id,
    )
    return ApiResponse(data={"candidates": [candidate], "task_id": task.id})


@router.post("/api/projects/{project_id}/shot-videos/generate", deprecated=True)
def generate_project_shot_videos_endpoint(
    project_id: str,
    db: Session = Depends(get_db),
) -> None:
    _ = (project_id, db)
    _raise_candidate_required("/api/projects/{project_id}/shot-videos/generate-candidates")


@router.post(
    "/api/projects/{project_id}/shot-videos/generate-candidates",
    response_model=ApiResponse[GenerationTaskRead],
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_project_shot_video_candidates_endpoint(
    project_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> ApiResponse[GenerationTaskRead]:
    task = create_project_video_batch_task(
        db,
        project_id,
        idempotency_key=idempotency_key,
    )
    get_task_runtime().wake()
    return ApiResponse(data=task)


@router.post("/api/shots/{shot_id}/video/generate", deprecated=True)
def generate_single_shot_video_endpoint(
    shot_id: str,
    payload: ShotVideoGenerationRequest,
    db: Session = Depends(get_db),
) -> None:
    _ = (shot_id, payload, db)
    _raise_candidate_required("/api/shots/{shot_id}/video/generate-candidate")


@router.post(
    "/api/shots/{shot_id}/video/generate-candidate",
    response_model=ApiResponse[GenerationTaskRead],
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_single_shot_video_candidate_endpoint(
    shot_id: str,
    payload: ShotVideoGenerationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> ApiResponse[GenerationTaskRead]:
    task = create_video_candidate_task(
        db,
        shot_id,
        duration_mode=payload.duration_mode,
        duration_sec=payload.duration_sec,
        video_prompt=payload.video_prompt,
        idempotency_key=idempotency_key,
    )
    get_task_runtime().wake()
    return ApiResponse(data=task)




@router.post("/api/assets/{asset_id}/select", response_model=ApiResponse[AssetRead])
def select_asset_endpoint(
    asset_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse[AssetRead]:
    return ApiResponse(data=select_asset(db, asset_id))


@router.post("/api/projects/{project_id}/assets/images/upload", response_model=ApiResponse[AssetRead])
async def upload_project_image_asset_endpoint(
    project_id: str,
    entity_type: str = Form(...),
    entity_id: str = Form(...),
    asset_role: str | None = Form(default=None),
    variant_key: str = Form(default="base"),
    prompt: str | None = Form(default=None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> ApiResponse[AssetRead]:
    content = await file.read()
    return ApiResponse(
        data=upload_image_asset(
            db,
            project_id,
            content=content,
            filename=file.filename or "upload.png",
            content_type=file.content_type,
            entity_type=entity_type,
            entity_id=entity_id,
            asset_role=asset_role,
            variant_key=variant_key,
            prompt=prompt,
        )
    )


@router.post("/api/assets/{asset_id}/regenerate", deprecated=True)
def regenerate_image_asset_endpoint(
    asset_id: str,
    db: Session = Depends(get_db),
) -> None:
    _ = (asset_id, db)
    _raise_candidate_required("/api/assets/{asset_id}/regenerate-candidate")


@router.post("/api/assets/{asset_id}/regenerate-candidate", response_model=ApiResponse[AssetCandidateGenerationResponse])
def regenerate_image_asset_candidate_endpoint(
    asset_id: str,
    image_provider_profile_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ApiResponse[AssetCandidateGenerationResponse]:
    candidate, task = regenerate_image_asset_candidate(
        db,
        asset_id,
        image_provider_profile_id=image_provider_profile_id,
    )
    return ApiResponse(data={"candidates": [candidate], "task_id": task.id})


@router.post("/api/assets/{asset_id}/regenerate-video", deprecated=True)
def regenerate_video_asset_endpoint(
    asset_id: str,
    db: Session = Depends(get_db),
) -> None:
    _ = (asset_id, db)
    _raise_candidate_required("/api/assets/{asset_id}/regenerate-video-candidate")


@router.post(
    "/api/assets/{asset_id}/regenerate-video-candidate",
    response_model=ApiResponse[GenerationTaskRead],
    status_code=status.HTTP_202_ACCEPTED,
)
def regenerate_video_asset_candidate_endpoint(
    asset_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> ApiResponse[GenerationTaskRead]:
    task = create_video_regeneration_task(
        db,
        asset_id,
        idempotency_key=idempotency_key,
    )
    get_task_runtime().wake()
    return ApiResponse(data=task)


@router.post("/api/assets/{asset_id}/extract-frame", response_model=ApiResponse[AssetCandidateGenerationResponse])
def extract_video_frame_candidate_endpoint(
    asset_id: str,
    time_sec: float = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> ApiResponse[AssetCandidateGenerationResponse]:
    candidate, task = extract_video_frame_candidate(
        db,
        asset_id,
        time_sec=Decimal(str(time_sec)),
    )
    return ApiResponse(data={"candidates": [candidate], "task_id": task.id})


@router.delete("/api/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset_endpoint(
    asset_id: str,
    db: Session = Depends(get_db),
) -> Response:
    delete_asset(db, asset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _raise_candidate_required(candidate_endpoint: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "candidate_required",
            "message": "模型输出必须先进入候选资产；请采用一个版本后再用于后续生成。",
            "candidate_endpoint": candidate_endpoint,
        },
    )
