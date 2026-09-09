from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, AssetCandidate, GenerationTask, Shot
from app.providers.types import ProviderResponse, ProviderStatus
from app.services.artifact_idempotency import artifact_completion_key
from app.services.asset_candidate_service import promote_asset_candidate
from app.services.asset_repository import (
    asset_source_context,
    infer_asset_role,
    next_candidate_version,
    resolve_source_stage_run_id,
)
from app.services.media_generation_support import sanitize_large_payload, with_avd_asset_usage
from app.services.media_storage_service import materialize_data_uri
from app.services.task_service import (
    TaskCompletionConflictError,
    begin_task_completion,
    mark_task_succeeded,
)
from app.services.workflow_state_service import (
    mark_downstream_stages_pending,
    mark_stage_approved,
    mark_stage_ready,
)


def persist_video_candidate_task_result(
    db: Session,
    task: GenerationTask,
    response: ProviderResponse,
) -> AssetCandidate:
    """Persist and adopt a candidate from a frozen task snapshot."""
    if not begin_task_completion(db, task):
        existing = _completed_video_candidate(db, task)
        if existing is None:
            raise TaskCompletionConflictError(
                f"Succeeded video task {task.id} has no persisted candidate"
            )
        return existing

    payload = task.input_payload if isinstance(task.input_payload, dict) else {}
    request_payload = payload.get("provider_request")
    if not isinstance(request_payload, dict):
        raise ValueError("Video task provider request snapshot is missing")
    shot_id = str(payload.get("shot_id") or "")
    shot = db.get(Shot, shot_id)
    if shot is None:
        raise ValueError("Video task shot no longer exists")
    candidate = persist_provider_video_candidate(
        db,
        project_id=task.project_id,
        entity_type="shot",
        entity_id=shot_id,
        provider_name=str(task.provider or ""),
        model=str(task.model or request_payload.get("model") or ""),
        prompt=str(request_payload.get("prompt") or ""),
        negative_prompt=(
            str(request_payload.get("negative_prompt"))
            if request_payload.get("negative_prompt") is not None
            else None
        ),
        response=response,
        source_task_id=task.id,
        request_metadata=(
            payload.get("request_metadata")
            if isinstance(payload.get("request_metadata"), dict)
            else {}
        ),
    )

    source_candidate_id = str(payload.get("source_candidate_id") or "")
    if source_candidate_id:
        source_candidate = db.get(AssetCandidate, source_candidate_id)
        if source_candidate is not None and source_candidate.status == "pending_review":
            source_candidate.status = "rejected"
            source_candidate.review_note = f"已由重新生成候选 {candidate.id} 替代"
            source_candidate.rejected_at = datetime.now(timezone.utc)
            db.add(source_candidate)
        candidate.raw_response = {
            **dict(candidate.raw_response or {}),
            "regeneration": {
                "source_candidate_id": source_candidate_id,
                "source_candidate_version": payload.get("source_candidate_version"),
            },
        }
        db.add(candidate)

    promote_asset_candidate(
        db,
        candidate.id,
        review_note="生成成功后自动采用为当前版本",
        commit=False,
    )
    current_shot_ids = set(
        db.scalars(
            select(Shot.id)
            .where(Shot.project_id == shot.project_id)
            .where(Shot.is_current.is_(True))
        ).all()
    )
    selected_video_shot_ids = set(
        db.scalars(
            select(Asset.entity_id)
            .where(Asset.project_id == shot.project_id)
            .where(Asset.asset_type == "video")
            .where(Asset.entity_type == "shot")
            .where(Asset.status == "approved")
            .where(Asset.is_selected.is_(True))
        ).all()
    )
    if current_shot_ids and current_shot_ids.issubset(selected_video_shot_ids):
        mark_stage_approved(
            db,
            shot.project_id,
            "videos",
            summary=f"{len(current_shot_ids)} 个镜头视频已生成并自动采用",
            metadata={"asset_count": len(current_shot_ids), "auto_adopt": True},
        )
    else:
        mark_stage_ready(
            db,
            shot.project_id,
            "videos",
            task_id=task.id,
            summary=f"镜头 {shot.shot_no} 视频已生成并设为当前版本",
            metadata={
                "candidate_count": 1,
                "kind": task.task_type,
                "shot_id": shot.id,
            },
        )
    mark_downstream_stages_pending(
        db,
        shot.project_id,
        "videos",
        summary="视频版本已更新，需要重新导出",
    )
    mark_task_succeeded(
        db,
        task,
        result_payload={
            "candidate_ids": [candidate.id],
            "candidate_count": 1,
            "candidate_policy": "pending_review_before_asset",
        },
        raw_response={
            **dict(task.raw_response or {}),
            "provider_result": sanitize_large_payload(response.raw_response),
        },
        provider_task_id=response.provider_task_id,
    )
    db.add(task)
    db.commit()
    db.refresh(candidate)
    db.refresh(task)
    return candidate


def persist_provider_video_candidate(
    db: Session,
    *,
    project_id: str,
    entity_type: str,
    entity_id: str,
    provider_name: str,
    model: str,
    prompt: str,
    negative_prompt: str | None,
    response: ProviderResponse,
    source_task_id: str | None = None,
    request_metadata: dict | None = None,
) -> AssetCandidate:
    if response.status != ProviderStatus.SUCCEEDED or not response.assets:
        if response.error:
            detail = {
                "message": response.error.error_message,
                "code": response.error.error_code,
                "provider": provider_name,
                "model": model,
                "retryable": response.error.is_retryable,
            }
        else:
            detail = {
                "message": "Video provider failed",
                "code": "video_provider_failed",
                "provider": provider_name,
                "model": model,
            }
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)

    provider_asset = response.assets[0]
    resolved_asset_role = infer_asset_role(
        asset_type="video",
        entity_type=entity_type,
        entity_id=entity_id,
    )
    request_metadata = with_avd_asset_usage(
        request_metadata,
        asset_type="video",
        asset_role=resolved_asset_role,
        entity_type=entity_type,
    )
    version = next_candidate_version(
        db,
        project_id,
        "video",
        entity_type,
        entity_id,
        resolved_asset_role,
    )
    source_script_id, source_shot_batch_id = asset_source_context(
        db,
        entity_type,
        entity_id,
    )
    source_stage_run_id = resolve_source_stage_run_id(db, project_id, source_task_id)
    candidate_id = str(uuid4())

    candidate = AssetCandidate(
        id=candidate_id,
        project_id=project_id,
        asset_type="video",
        asset_role=resolved_asset_role,
        entity_type=entity_type,
        entity_id=entity_id,
        variant_key="base" if entity_type in {"character", "scene", "prop"} else None,
        source_task_id=source_task_id,
        completion_key=artifact_completion_key(
            source_task_id,
            artifact_kind="candidate",
            candidate_type="generated",
            asset_type="video",
            asset_role=resolved_asset_role,
            entity_type=entity_type,
            entity_id=entity_id,
            variant_key="base" if entity_type in {"character", "scene", "prop"} else None,
        ),
        source_stage_run_id=source_stage_run_id,
        source_script_id=source_script_id,
        source_shot_batch_id=source_shot_batch_id,
        candidate_type="generated",
        version=version,
        uri=materialize_data_uri(
            project_id,
            candidate_id,
            provider_asset.uri,
            provider_asset.mime_type,
        ),
        mime_type=provider_asset.mime_type,
        width=provider_asset.width,
        height=provider_asset.height,
        duration_sec=provider_asset.duration_sec,
        provider=provider_name,
        model=model,
        prompt=prompt,
        negative_prompt=negative_prompt,
        raw_response={
            "provider_response": sanitize_large_payload(response.raw_response),
            "request_metadata": request_metadata,
        },
        status="pending_review",
    )
    db.add(candidate)
    db.flush()
    return candidate


def _completed_video_candidate(
    db: Session,
    task: GenerationTask,
) -> AssetCandidate | None:
    result = task.result_payload if isinstance(task.result_payload, dict) else {}
    candidate_ids = result.get("candidate_ids")
    if isinstance(candidate_ids, list):
        for candidate_id in candidate_ids:
            candidate = db.get(AssetCandidate, str(candidate_id))
            if candidate is not None and candidate.source_task_id == task.id:
                return candidate
    return db.scalar(
        select(AssetCandidate)
        .where(AssetCandidate.source_task_id == task.id)
        .where(AssetCandidate.asset_type == "video")
        .order_by(AssetCandidate.created_at, AssetCandidate.id)
        .limit(1)
    )
