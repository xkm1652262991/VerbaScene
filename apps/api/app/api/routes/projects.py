import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.models import Asset
from app.db import get_db
from app.schemas.common import ApiResponse, PageResponse
from app.schemas.project import (
    ChapterUpsert,
    PatchImpactRead,
    PatchImpactRequest,
    ProjectCreate,
    ProjectDeleteRequest,
    ProjectDeleteResult,
    ProjectDeletionPreview,
    ProjectDetail,
    ProjectRead,
    ProjectUpdate,
)
from app.services.project_service import (
    create_project,
    delete_project,
    get_project,
    get_project_deletion_preview,
    list_projects_page,
    update_project,
    upsert_project_chapter,
)
from app.services.asset_repository import list_assets, list_current_assets
from app.services.asset_candidate_service import list_asset_candidates
from app.services.dialogue_service import list_dialogues
from app.services.entity_service import get_current_entities, list_all_entities
from app.services.export_service import list_exports
from app.services.pre_image_service import build_project_prompt_previews
from app.services.patch_pipeline_service import analyze_patch_impact
from app.services.quality_service import list_quality_checks
from app.services.readiness_service import get_project_readiness
from app.scripts.service import get_latest_script
from app.services.shot_frame_service import list_shot_frame_images, list_shot_frame_prompts
from app.services.shot_service import list_shots
from app.services.stage_run_service import list_project_stage_runs

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", response_model=ApiResponse[ProjectDetail], status_code=status.HTTP_201_CREATED)
def create_project_endpoint(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
) -> ApiResponse[ProjectDetail]:
    return ApiResponse(data=create_project(db, payload))


@router.get("", response_model=PageResponse[ProjectRead])
def list_projects_endpoint(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> PageResponse[ProjectRead]:
    projects, total = list_projects_page(db, limit=limit, offset=offset)
    return PageResponse(items=projects, meta={"total": total, "offset": offset, "limit": limit})


@router.get("/{project_id}", response_model=ApiResponse[ProjectDetail])
def get_project_endpoint(
    project_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse[ProjectDetail]:
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return ApiResponse(data=project)


@router.get("/{project_id}/deletion-preview", response_model=ApiResponse[ProjectDeletionPreview])
def get_project_deletion_preview_endpoint(
    project_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse[ProjectDeletionPreview]:
    return ApiResponse(data=get_project_deletion_preview(db, project_id))


@router.delete("/{project_id}", response_model=ApiResponse[ProjectDeleteResult])
def delete_project_endpoint(
    project_id: str,
    payload: ProjectDeleteRequest,
    db: Session = Depends(get_db),
) -> ApiResponse[ProjectDeleteResult]:
    return ApiResponse(data=delete_project(db, project_id, payload))


@router.get("/{project_id}/workbench", response_model=ApiResponse[dict])
def get_project_workbench_endpoint(
    project_id: str,
    db: Session = Depends(get_db),
) -> ApiResponse[dict]:
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    current_characters, current_scenes, current_props = get_current_entities(db, project_id)
    all_characters, all_scenes, all_props = list_all_entities(db, project_id)
    historical_entities = {
        "characters": all_characters,
        "scenes": all_scenes,
        "props": all_props,
    }
    current_entities = {
        "characters": current_characters,
        "scenes": current_scenes,
        "props": current_props,
    }

    current_assets = [
        asset
        for asset in list_current_assets(db, project_id)
        if asset.asset_type in {"image", "video", "final_video"}
    ]
    historical_assets = [
        asset
        for asset in list_assets(db, project_id)
        if asset.asset_type in {"image", "video", "final_video"}
    ]
    asset_candidates = list_asset_candidates(db, project_id)
    return ApiResponse(
        data=jsonable_encoder({
            "project": jsonable_encoder(project, exclude={"director_memory"}),
            "script": get_latest_script(db, project_id),
            "entities": current_entities,
            "current_entities": current_entities,
            "historical_entities": historical_entities,
            "shots": list_shots(db, project_id),
            "prompt_previews": build_project_prompt_previews(db, project_id),
            "assets": [_asset_payload(asset) for asset in current_assets],
            "current_assets": [_asset_payload(asset) for asset in current_assets],
            "historical_assets": [_asset_payload(asset) for asset in historical_assets],
            "asset_candidates": asset_candidates,
            "dialogues": list_dialogues(db, project_id),
            "exports": list_exports(db, project_id),
            "frame_prompts": list_shot_frame_prompts(db, project_id),
            "frame_images": list_shot_frame_images(db, project_id),
            "quality_checks": list_quality_checks(db, project_id),
            "readiness": get_project_readiness(db, project_id),
            "stage_runs": list_project_stage_runs(db, project_id, limit=20),
        })
    )


@router.patch("/{project_id}", response_model=ApiResponse[ProjectDetail])
def update_project_endpoint(
    project_id: str,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
) -> ApiResponse[ProjectDetail]:
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return ApiResponse(data=update_project(db, project, payload))


@router.post("/{project_id}/patch-impact", response_model=ApiResponse[PatchImpactRead])
def analyze_project_patch_impact_endpoint(
    project_id: str,
    payload: PatchImpactRequest,
    db: Session = Depends(get_db),
) -> ApiResponse[PatchImpactRead]:
    return ApiResponse(
        data=analyze_patch_impact(
            db,
            project_id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            field=payload.field,
            change_summary=payload.change_summary,
        )
    )


@router.put("/{project_id}/chapter", response_model=ApiResponse[ProjectDetail])
def upsert_project_chapter_endpoint(
    project_id: str,
    payload: ChapterUpsert,
    db: Session = Depends(get_db),
) -> ApiResponse[ProjectDetail]:
    project = get_project(db, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return ApiResponse(data=upsert_project_chapter(db, project, payload))


@router.get("/{project_id}/events")
async def project_events_endpoint(project_id: str) -> StreamingResponse:
    async def stream():
        yield f"event: connected\ndata: {json.dumps({'project_id': project_id})}\n\n"
        while True:
            await asyncio.sleep(10)
            yield f"event: heartbeat\ndata: {json.dumps({'project_id': project_id})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


def _asset_payload(asset: Asset) -> dict:
    return {
        "id": asset.id,
        "project_id": asset.project_id,
        "asset_type": asset.asset_type,
        "asset_role": asset.asset_role,
        "entity_type": asset.entity_type,
        "entity_id": asset.entity_id,
        "variant_key": asset.variant_key,
        "source_task_id": asset.source_task_id,
        "source_stage_run_id": asset.source_stage_run_id,
        "source_script_id": asset.source_script_id,
        "source_shot_batch_id": asset.source_shot_batch_id,
        "version": asset.version,
        "uri": _safe_asset_uri(asset.uri),
        "mime_type": asset.mime_type,
        "width": asset.width,
        "height": asset.height,
        "duration_sec": asset.duration_sec,
        "provider": asset.provider,
        "model": asset.model,
        "prompt": asset.prompt,
        "negative_prompt": asset.negative_prompt,
        "raw_response": {},
        "status": asset.status,
        "is_selected": asset.is_selected,
        "created_at": asset.created_at,
        "updated_at": asset.updated_at,
    }


def _safe_asset_uri(uri: str) -> str:
    if uri.startswith("data:") and len(uri) > 4096:
        return ""
    return uri
