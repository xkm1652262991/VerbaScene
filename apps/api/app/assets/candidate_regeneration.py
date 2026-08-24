from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import AssetCandidate, GenerationTask
from app.production.video_tasks import create_video_candidate_regeneration_task
from app.services.asset_candidate_service import OPEN_CANDIDATE_STATUSES
from app.services.image_generation_service import regenerate_image_candidate_from_candidate


@dataclass(frozen=True)
class CandidateRegenerationResult:
    task: GenerationTask
    candidate: AssetCandidate | None

    @property
    def is_async(self) -> bool:
        return self.candidate is None


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
        return CandidateRegenerationResult(task=task, candidate=None)
    if source_candidate.asset_type != "image":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only image and video candidates can be regenerated",
        )

    replacement, task = regenerate_image_candidate_from_candidate(
        db,
        source_candidate,
        image_provider_profile_id=image_provider_profile_id,
    )
    source_candidate.status = "rejected"
    source_candidate.review_note = f"已由重新生成候选 {replacement.id} 替代"
    source_candidate.rejected_at = datetime.now(timezone.utc)
    replacement.raw_response = {
        **(replacement.raw_response or {}),
        "regeneration": {
            "source_candidate_id": source_candidate.id,
            "source_candidate_version": source_candidate.version,
        },
    }
    task.input_payload = {
        **(task.input_payload or {}),
        "source_candidate_id": source_candidate.id,
        "source_candidate_version": source_candidate.version,
    }
    db.add_all([source_candidate, replacement, task])
    db.commit()
    db.refresh(source_candidate)
    db.refresh(replacement)
    db.refresh(task)
    return CandidateRegenerationResult(task=task, candidate=replacement)
