from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.common import ApiResponse, PageResponse
from app.schemas.asset import (
    AssetCandidateCreate,
    AssetCandidateGenerationResponse,
    AssetCandidateRead,
    AssetCandidateReviewRequest,
    AssetRead,
)
from app.services.asset_candidate_service import (
    create_asset_candidate,
    delete_asset_candidate,
    list_asset_candidates_page,
    promote_asset_candidate,
    regenerate_asset_candidate,
    reject_asset_candidate,
)

router = APIRouter(tags=["asset-candidates"])


@router.get("/api/projects/{project_id}/asset-candidates", response_model=PageResponse[AssetCandidateRead])
def list_project_asset_candidates_endpoint(
    project_id: str,
    status: str | None = Query(default=None),
    asset_type: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> PageResponse[AssetCandidateRead]:
    candidates, total = list_asset_candidates_page(
        db,
        project_id,
        status_filter=status,
        asset_type=asset_type,
        entity_type=entity_type,
        offset=offset,
        limit=limit,
    )
    return PageResponse(items=candidates, meta={"total": total, "offset": offset, "limit": limit})


@router.post("/api/projects/{project_id}/asset-candidates", response_model=ApiResponse[AssetCandidateRead])
def create_project_asset_candidate_endpoint(
    project_id: str,
    payload: AssetCandidateCreate,
    db: Session = Depends(get_db),
) -> ApiResponse[AssetCandidateRead]:
    return ApiResponse(data=create_asset_candidate(db, project_id, payload))


@router.post("/api/asset-candidates/{candidate_id}/promote", response_model=ApiResponse[AssetRead])
def promote_asset_candidate_endpoint(
    candidate_id: str,
    payload: AssetCandidateReviewRequest | None = None,
    db: Session = Depends(get_db),
) -> ApiResponse[AssetRead]:
    return ApiResponse(data=promote_asset_candidate(db, candidate_id, review_note=payload.review_note if payload else None))


@router.post("/api/asset-candidates/{candidate_id}/reject", response_model=ApiResponse[AssetCandidateRead])
def reject_asset_candidate_endpoint(
    candidate_id: str,
    payload: AssetCandidateReviewRequest | None = None,
    db: Session = Depends(get_db),
) -> ApiResponse[AssetCandidateRead]:
    return ApiResponse(data=reject_asset_candidate(db, candidate_id, review_note=payload.review_note if payload else None))


@router.post(
    "/api/asset-candidates/{candidate_id}/regenerate",
    response_model=ApiResponse[AssetCandidateGenerationResponse],
)
def regenerate_asset_candidate_endpoint(
    candidate_id: str,
    image_provider_profile_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ApiResponse[AssetCandidateGenerationResponse]:
    candidate, task = regenerate_asset_candidate(
        db,
        candidate_id,
        image_provider_profile_id=image_provider_profile_id,
    )
    return ApiResponse(data={"candidates": [candidate], "task_id": task.id})


@router.delete("/api/asset-candidates/{candidate_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset_candidate_endpoint(
    candidate_id: str,
    db: Session = Depends(get_db),
) -> None:
    delete_asset_candidate(db, candidate_id)
