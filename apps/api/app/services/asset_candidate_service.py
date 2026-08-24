from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Asset, AssetCandidate, GenerationTask
from app.platform.tasks.types import ACTIVE_TASK_STATUSES
from app.schemas.asset import AssetCandidateCreate
from app.services.asset_lifecycle_service import apply_actual_video_duration_to_shot
from app.services.asset_repository import (
    get_project_or_404,
    next_asset_version,
    next_candidate_version,
)
from app.services.media_storage_service import unlink_local_storage_file
from app.services.workflow_state_service import mark_downstream_stages_pending


OPEN_CANDIDATE_STATUSES = {"pending_review"}
DELETABLE_CANDIDATE_STATUSES = {"pending_review", "rejected"}


def list_asset_candidates(
    db: Session,
    project_id: str,
    *,
    status_filter: str | None = None,
    asset_type: str | None = None,
    entity_type: str | None = None,
) -> list[AssetCandidate]:
    get_project_or_404(db, project_id)
    statement = select(AssetCandidate).where(AssetCandidate.project_id == project_id)
    if status_filter:
        statement = statement.where(AssetCandidate.status == status_filter)
    else:
        statement = statement.where(AssetCandidate.status != "deleted")
    if asset_type:
        statement = statement.where(AssetCandidate.asset_type == asset_type)
    if entity_type:
        statement = statement.where(AssetCandidate.entity_type == entity_type)
    return list(
        db.scalars(
            statement.order_by(
                AssetCandidate.status,
                AssetCandidate.created_at.desc(),
                AssetCandidate.entity_type,
                AssetCandidate.entity_id,
                AssetCandidate.version.desc(),
            )
        ).all()
    )


def list_asset_candidates_page(
    db: Session,
    project_id: str,
    *,
    status_filter: str | None = None,
    asset_type: str | None = None,
    entity_type: str | None = None,
    offset: int = 0,
    limit: int = 500,
) -> tuple[list[AssetCandidate], int]:
    get_project_or_404(db, project_id)
    statement = select(AssetCandidate).where(AssetCandidate.project_id == project_id)
    count_statement = select(func.count(AssetCandidate.id)).where(AssetCandidate.project_id == project_id)
    if status_filter:
        statement = statement.where(AssetCandidate.status == status_filter)
        count_statement = count_statement.where(AssetCandidate.status == status_filter)
    else:
        statement = statement.where(AssetCandidate.status != "deleted")
        count_statement = count_statement.where(AssetCandidate.status != "deleted")
    if asset_type:
        statement = statement.where(AssetCandidate.asset_type == asset_type)
        count_statement = count_statement.where(AssetCandidate.asset_type == asset_type)
    if entity_type:
        statement = statement.where(AssetCandidate.entity_type == entity_type)
        count_statement = count_statement.where(AssetCandidate.entity_type == entity_type)
    ordered = statement.order_by(
        AssetCandidate.status,
        AssetCandidate.created_at.desc(),
        AssetCandidate.entity_type,
        AssetCandidate.entity_id,
        AssetCandidate.version.desc(),
    )
    total = db.scalar(count_statement) or 0
    candidates = list(db.scalars(ordered.offset(offset).limit(limit)).all())
    return candidates, total


def create_asset_candidate(
    db: Session,
    project_id: str,
    payload: AssetCandidateCreate,
) -> AssetCandidate:
    get_project_or_404(db, project_id)
    if payload.asset_type not in {"image", "video"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unsupported candidate asset type")
    candidate = AssetCandidate(
        project_id=project_id,
        asset_id=None,
        source_task_id=payload.source_task_id,
        source_stage_run_id=payload.source_stage_run_id,
        source_script_id=payload.source_script_id,
        source_shot_batch_id=payload.source_shot_batch_id,
        candidate_type=payload.candidate_type,
        asset_type=payload.asset_type,
        asset_role=payload.asset_role,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        variant_key=payload.variant_key or (
            "base" if payload.entity_type in {"character", "scene", "prop"} else None
        ),
        version=next_candidate_version(
            db,
            project_id,
            payload.asset_type,
            payload.entity_type,
            payload.entity_id,
            payload.asset_role,
            payload.variant_key or (
                "base" if payload.entity_type in {"character", "scene", "prop"} else None
            ),
        ),
        uri=payload.uri,
        mime_type=payload.mime_type,
        width=payload.width,
        height=payload.height,
        duration_sec=payload.duration_sec,
        provider=payload.provider,
        model=payload.model,
        prompt=payload.prompt,
        negative_prompt=payload.negative_prompt,
        raw_response=payload.raw_response,
        status="pending_review",
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate


def promote_asset_candidate(
    db: Session,
    candidate_id: str,
    *,
    review_note: str | None = None,
    commit: bool = True,
) -> Asset:
    candidate = db.get(AssetCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset candidate not found")
    if candidate.status not in OPEN_CANDIDATE_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Asset candidate is not pending review")

    db.execute(
        update(Asset)
        .where(Asset.project_id == candidate.project_id)
        .where(Asset.asset_type == candidate.asset_type)
        .where(Asset.asset_role == candidate.asset_role)
        .where(Asset.entity_type == candidate.entity_type)
        .where(Asset.entity_id == candidate.entity_id)
        .where(Asset.variant_key == candidate.variant_key)
        .values(is_selected=False),
    )
    asset = Asset(
        project_id=candidate.project_id,
        asset_type=candidate.asset_type,
        asset_role=candidate.asset_role,
        entity_type=candidate.entity_type,
        entity_id=candidate.entity_id,
        variant_key=candidate.variant_key,
        source_task_id=candidate.source_task_id,
        source_stage_run_id=candidate.source_stage_run_id,
        source_script_id=candidate.source_script_id,
        source_shot_batch_id=candidate.source_shot_batch_id,
        version=next_asset_version(
            db,
            candidate.project_id,
            candidate.asset_type,
            candidate.entity_type,
            candidate.entity_id,
            candidate.asset_role,
            candidate.variant_key,
        ),
        uri=candidate.uri,
        mime_type=candidate.mime_type,
        width=candidate.width,
        height=candidate.height,
        duration_sec=candidate.duration_sec,
        provider=candidate.provider,
        model=candidate.model,
        prompt=candidate.prompt,
        negative_prompt=candidate.negative_prompt,
        raw_response={
            **(candidate.raw_response or {}),
            "candidate_id": candidate.id,
            "candidate_type": candidate.candidate_type,
        },
        status="approved",
        is_selected=True,
    )
    db.add(asset)
    db.flush()
    if asset.asset_type == "video":
        request_metadata = (
            candidate.raw_response.get("request_metadata")
            if isinstance(candidate.raw_response, dict)
            and isinstance(candidate.raw_response.get("request_metadata"), dict)
            else {}
        )
        apply_actual_video_duration_to_shot(
            db,
            entity_type=str(asset.entity_type or ""),
            entity_id=str(asset.entity_id or ""),
            duration_sec=asset.duration_sec,
            request_metadata=request_metadata,
        )
    candidate.status = "promoted"
    candidate.review_note = review_note
    candidate.promoted_asset_id = asset.id
    candidate.asset_id = asset.id
    candidate.promoted_at = datetime.now(timezone.utc)
    db.add(candidate)
    if asset.asset_type == "image":
        mark_downstream_stages_pending(db, asset.project_id, "images", summary="图片已更新，需要重新生成视频和后续内容")
    elif asset.asset_type == "video":
        mark_downstream_stages_pending(db, asset.project_id, "videos", summary="视频版本已采用，需要重新导出")
    if commit:
        db.commit()
        db.refresh(asset)
    else:
        db.flush()
    return asset


def reject_asset_candidate(
    db: Session,
    candidate_id: str,
    *,
    review_note: str | None = None,
) -> AssetCandidate:
    candidate = db.get(AssetCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset candidate not found")
    if candidate.status not in OPEN_CANDIDATE_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Asset candidate is not pending review")
    candidate.status = "rejected"
    candidate.review_note = review_note
    candidate.rejected_at = datetime.now(timezone.utc)
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate


def delete_asset_candidate(db: Session, candidate_id: str) -> None:
    candidate = db.get(AssetCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset candidate not found")
    blocking_task = db.scalar(
        select(GenerationTask)
        .where(GenerationTask.project_id == candidate.project_id)
        .where(GenerationTask.status.in_(tuple(ACTIVE_TASK_STATUSES)))
        .where(GenerationTask.input_payload["source_candidate_id"].as_string() == candidate.id)
    )
    if blocking_task is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "asset_candidate_in_use_by_active_task",
                "message": "候选资产已被活动重生成任务引用",
                "task_id": blocking_task.id,
            },
        )
    if candidate.status == "promoted" or candidate.promoted_asset_id or candidate.asset_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Promoted candidates must be removed through the formal asset deletion flow",
        )
    if candidate.status not in DELETABLE_CANDIDATE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only pending or rejected asset candidates can be deleted",
        )

    original_uri = candidate.uri
    deleted_at = datetime.now(timezone.utc)
    candidate.status = "deleted"
    candidate.uri = f"deleted://asset-candidate/{candidate.id}"
    candidate.review_note = "候选已删除，本地媒体文件已清理"
    candidate.raw_response = {
        **(candidate.raw_response or {}),
        "deletion": {
            "deleted_at": deleted_at.isoformat(),
            "media_policy": "local_file_unlinked",
        },
    }
    db.add(candidate)
    db.commit()
    unlink_local_storage_file(original_uri)
