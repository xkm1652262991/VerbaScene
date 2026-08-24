from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, AssetCandidate, GenerationTask, Shot
from app.platform.tasks.repository import (
    TaskConflictError,
    TaskRepository,
    active_dedupe_key,
)
from app.platform.tasks.types import TaskStatus
from app.production.video_request_compiler import compile_video_candidate_task_input
from app.services.asset_repository import get_project_or_404
from app.services.video_generation_service import VIDEO_CANDIDATE_TASK_TYPE
from app.services.workflow_state_service import mark_stage_running


PROJECT_VIDEO_BATCH_TASK_TYPE = "project_video_candidate_batch"


def create_video_candidate_task(
    db: Session,
    shot_id: str,
    *,
    duration_mode: str | None = None,
    duration_sec: Decimal | None = None,
    video_prompt: str | None = None,
    source_asset: Asset | None = None,
    source_candidate: AssetCandidate | None = None,
    idempotency_key: str | None = None,
    parent_task_id: str | None = None,
    update_shot: bool = True,
) -> GenerationTask:
    shot = db.get(Shot, shot_id)
    if shot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shot not found")
    if not shot.is_current:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Current shot is required before video generation",
        )
    normalized_idempotency_key = (idempotency_key or "").strip()
    if normalized_idempotency_key:
        existing = db.scalar(
            select(GenerationTask)
            .where(GenerationTask.project_id == shot.project_id)
            .where(GenerationTask.task_type == VIDEO_CANDIDATE_TASK_TYPE)
            .where(GenerationTask.idempotency_key == normalized_idempotency_key)
        )
        if existing is not None:
            return existing
    payload, provider = compile_video_candidate_task_input(
        db,
        shot,
        duration_mode=duration_mode,
        duration_sec=duration_sec,
        video_prompt=video_prompt,
        source_asset=source_asset,
        update_shot=update_shot,
    )
    payload["operation"] = "regenerate" if source_asset else "generate"
    if source_candidate is not None:
        payload["operation"] = "regenerate_candidate"
        payload["source_candidate_id"] = source_candidate.id
        payload["source_candidate_version"] = source_candidate.version
    try:
        creation = TaskRepository().create(
            db,
            project_id=shot.project_id,
            task_type=VIDEO_CANDIDATE_TASK_TYPE,
            parent_task_id=parent_task_id,
            resource_key=f"shot:{shot.id}:video",
            idempotency_key=normalized_idempotency_key or None,
            provider=provider.name,
            model=provider.model,
            input_payload=payload,
            progress_label="等待视频生成",
        )
    except TaskConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"镜头 {shot.shot_no} 已有活动视频任务",
                "task_id": exc.task_id,
                "conflict_shot_ids": [shot.id],
            },
        ) from exc
    if creation.created:
        mark_stage_running(db, shot.project_id, "videos", task_id=creation.task.id)
    db.commit()
    db.refresh(creation.task)
    return creation.task


def create_video_regeneration_task(
    db: Session,
    asset_id: str,
    *,
    idempotency_key: str | None = None,
) -> GenerationTask:
    source_asset = db.get(Asset, asset_id)
    if source_asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    if source_asset.asset_type != "video":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only video assets can be regenerated",
        )
    if source_asset.entity_type != "shot" or not source_asset.entity_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only shot video assets can be regenerated",
        )
    return create_video_candidate_task(
        db,
        source_asset.entity_id,
        source_asset=source_asset,
        idempotency_key=idempotency_key,
        update_shot=False,
    )


def create_video_candidate_regeneration_task(
    db: Session,
    candidate_id: str,
    *,
    idempotency_key: str | None = None,
) -> GenerationTask:
    source_candidate = db.get(AssetCandidate, candidate_id)
    if source_candidate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset candidate not found",
        )
    if source_candidate.status != "pending_review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Asset candidate is not pending review",
        )
    if (
        source_candidate.asset_type != "video"
        or source_candidate.entity_type != "shot"
        or not source_candidate.entity_id
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only shot video candidates can be regenerated",
        )
    return create_video_candidate_task(
        db,
        source_candidate.entity_id,
        source_candidate=source_candidate,
        idempotency_key=idempotency_key,
        update_shot=False,
    )


def create_project_video_batch_task(
    db: Session,
    project_id: str,
    *,
    idempotency_key: str | None = None,
) -> GenerationTask:
    get_project_or_404(db, project_id)
    if idempotency_key:
        existing = db.scalar(
            select(GenerationTask)
            .where(GenerationTask.project_id == project_id)
            .where(GenerationTask.task_type == PROJECT_VIDEO_BATCH_TASK_TYPE)
            .where(GenerationTask.idempotency_key == idempotency_key.strip())
        )
        if existing is not None:
            return existing
    shots = list(
        db.scalars(
            select(Shot)
            .where(Shot.project_id == project_id)
            .where(Shot.is_current.is_(True))
            .order_by(Shot.shot_no)
        ).all()
    )
    if not shots:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Video segments are required before generation",
        )
    task_keys = {
        shot.id: active_dedupe_key(
            VIDEO_CANDIDATE_TASK_TYPE,
            f"shot:{shot.id}:video",
        )
        for shot in shots
    }
    existing_conflicts = list(
        db.scalars(
            select(GenerationTask).where(
                GenerationTask.active_dedupe_key.in_(tuple(task_keys.values()))
            )
        ).all()
    )
    if existing_conflicts:
        _raise_batch_conflict(shots, existing_conflicts)

    repository = TaskRepository()
    try:
        parent_creation = repository.create(
            db,
            project_id=project_id,
            task_type=PROJECT_VIDEO_BATCH_TASK_TYPE,
            resource_key=f"project:{project_id}:videos",
            idempotency_key=idempotency_key,
            input_payload={"shot_ids": [shot.id for shot in shots]},
            status=TaskStatus.WAITING_CHILDREN,
            progress_label=f"等待子任务（0/{len(shots)}）",
            max_retries=0,
        )
        parent = parent_creation.task
        if not parent_creation.created:
            db.commit()
            db.refresh(parent)
            return parent
        child_ids: list[str] = []
        for shot in shots:
            payload, provider = compile_video_candidate_task_input(
                db,
                shot,
                update_shot=False,
            )
            payload["operation"] = "batch_generate"
            child = repository.create(
                db,
                project_id=project_id,
                task_type=VIDEO_CANDIDATE_TASK_TYPE,
                parent_task_id=parent.id,
                resource_key=f"shot:{shot.id}:video",
                provider=provider.name,
                model=provider.model,
                input_payload=payload,
                progress_label="等待视频生成",
            ).task
            child_ids.append(child.id)
        parent.result_payload = {
            "child_task_ids": child_ids,
            "summary": {"total": len(child_ids), "queued": len(child_ids)},
        }
        db.add(parent)
        mark_stage_running(db, project_id, "videos", task_id=parent.id)
        db.commit()
    except TaskConflictError as exc:
        db.rollback()
        conflicts = list(
            db.scalars(
                select(GenerationTask).where(
                    GenerationTask.active_dedupe_key.in_(tuple(task_keys.values()))
                )
            ).all()
        )
        if conflicts:
            _raise_batch_conflict(shots, conflicts)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "视频批次与活动任务冲突", "task_id": exc.task_id},
        ) from exc
    db.refresh(parent)
    return parent


def _raise_batch_conflict(shots: list[Shot], tasks: list[GenerationTask]) -> None:
    task_keys = {task.active_dedupe_key for task in tasks}
    conflict_shot_ids = [
        shot.id
        for shot in shots
        if active_dedupe_key(
            VIDEO_CANDIDATE_TASK_TYPE,
            f"shot:{shot.id}:video",
        )
        in task_keys
    ]
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "message": "批次中存在已有活动视频任务的镜头",
            "conflict_shot_ids": conflict_shot_ids,
            "conflict_task_ids": [task.id for task in tasks],
        },
    )
