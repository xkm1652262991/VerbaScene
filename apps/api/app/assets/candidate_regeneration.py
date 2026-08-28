from dataclasses import dataclass
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import AssetCandidate, GenerationTask
from app.production.video_tasks import create_video_candidate_regeneration_task
from app.services.asset_candidate_service import OPEN_CANDIDATE_STATUSES
from app.assets.image_tasks import create_image_candidate_regeneration_task


@dataclass(frozen=True)
class CandidateRegenerationResult:
    task: GenerationTask


def regenerate_asset_candidate(
    db: Session,
    candidate_id: str,
    *,
    image_provider_profile_id: str | None = None,
    idempotency_key: str | None = None,
) -> CandidateRegenerationResult:
    source_candidate = db.get(AssetCandidate, candidate_id)
    if source_candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset candidate not found",
        )
    if source_candidate.status not in OPEN_CANDIDATE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Asset candidate is not pending review",
        )
    if source_candidate.asset_type == "video":
        task = create_video_candidate_regeneration_task(
            db,
            source_candidate.id,
            idempotency_key=idempotency_key,
        )
        return CandidateRegenerationResult(task=task)
    if source_candidate.asset_type != "image":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only image and video candidates can be regenerated",
        )

    task = create_image_candidate_regeneration_task(
        db,
        source_candidate.id,
        image_provider_profile_id=image_provider_profile_id,
        idempotency_key=idempotency_key,
    )
    return CandidateRegenerationResult(task=task)
