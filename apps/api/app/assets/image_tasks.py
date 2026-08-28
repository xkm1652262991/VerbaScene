"""Creation use cases for durable image candidate tasks."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assets.contracts import (
    IMAGE_CANDIDATE_TASK_TYPE,
    REFERENCE_IMAGE_BATCH_TASK_TYPE,
    SHOT_IMAGE_BATCH_TASK_TYPE,
)
from app.models import Asset, AssetCandidate, GenerationTask
from app.platform.tasks.repository import (
    TaskConflictError,
    TaskRepository,
    active_dedupe_key,
)
from app.platform.tasks.types import TaskStatus
from app.services.image_generation_service import (
    ImageCandidateTarget,
    compile_image_candidate_task_input,
    image_candidate_provider_profile_id,
    prepare_image_regeneration_candidate_target,
    prepare_reference_image_candidate_targets,
    prepare_shot_image_candidate_targets,
)
from app.services.image_provider_profile_service import (
    ImageProviderSelection,
    resolve_image_provider_selection,
)
from app.services.workflow_state_service import mark_stage_running


def create_reference_image_batch_task(
    db: Session,
    project_id: str,
    *,
    image_provider_profile_id: str | None = None,
    idempotency_key: str | None = None,
) -> GenerationTask:
    existing = _idempotent_task(
        db,
        project_id,
        REFERENCE_IMAGE_BATCH_TASK_TYPE,
        idempotency_key,
    )
    if existing is not None:
        return existing
    targets = prepare_reference_image_candidate_targets(db, project_id)
    selection = resolve_image_provider_selection(db, image_provider_profile_id)
    return _create_image_batch(
        db,
        project_id=project_id,
        parent_task_type=REFERENCE_IMAGE_BATCH_TASK_TYPE,
        parent_resource_key=f"project:{project_id}:reference-images",
        operation="reference_image",
        targets=targets,
        selection=selection,
        idempotency_key=idempotency_key,
    )


def create_single_reference_image_task(
    db: Session,
    project_id: str,
    *,
    entity_type: str,
    entity_id: str,
    asset_role: str,
    variant_key: str = "base",
    image_provider_profile_id: str | None = None,
    idempotency_key: str | None = None,
) -> GenerationTask:
    existing = _idempotent_task(
        db,
        project_id,
        IMAGE_CANDIDATE_TASK_TYPE,
        idempotency_key,
    )
    if existing is not None:
        return existing
    target = prepare_reference_image_candidate_targets(
        db,
        project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        asset_role=asset_role,
        variant_key=variant_key,
    )[0]
    selection = resolve_image_provider_selection(db, image_provider_profile_id)
    return _create_single_image_task(
        db,
        target=target,
        selection=selection,
        operation="single_reference_image",
        idempotency_key=idempotency_key,
    )


def create_shot_image_batch_task(
    db: Session,
    project_id: str,
    *,
    image_provider_profile_id: str | None = None,
    idempotency_key: str | None = None,
) -> GenerationTask:
    existing = _idempotent_task(
        db,
        project_id,
        SHOT_IMAGE_BATCH_TASK_TYPE,
        idempotency_key,
    )
    if existing is not None:
        return existing
    targets = prepare_shot_image_candidate_targets(db, project_id)
    selection = resolve_image_provider_selection(db, image_provider_profile_id)
    return _create_image_batch(
        db,
        project_id=project_id,
        parent_task_type=SHOT_IMAGE_BATCH_TASK_TYPE,
        parent_resource_key=f"project:{project_id}:shot-images",
        operation="shot_image",
        targets=targets,
        selection=selection,
        idempotency_key=idempotency_key,
    )


def create_single_shot_image_task(
    db: Session,
    shot_id: str,
    *,
    image_provider_profile_id: str | None = None,
    idempotency_key: str | None = None,
) -> GenerationTask:
    target = _prepare_shot_target(db, shot_id)
    existing = _idempotent_task(
        db,
        target.project_id,
        IMAGE_CANDIDATE_TASK_TYPE,
        idempotency_key,
    )
    if existing is not None:
        return existing
    selection = resolve_image_provider_selection(db, image_provider_profile_id)
    return _create_single_image_task(
        db,
        target=target,
        selection=selection,
        operation="single_shot_image",
        idempotency_key=idempotency_key,
    )


def create_image_asset_regeneration_task(
    db: Session,
    asset_id: str,
    *,
    image_provider_profile_id: str | None = None,
    idempotency_key: str | None = None,
) -> GenerationTask:
    source = db.get(Asset, asset_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    existing = _idempotent_task(
        db,
        source.project_id,
        IMAGE_CANDIDATE_TASK_TYPE,
        idempotency_key,
    )
    if existing is not None:
        return existing
    target = prepare_image_regeneration_candidate_target(db, source)
    selection = resolve_image_provider_selection(db, image_provider_profile_id)
    return _create_single_image_task(
        db,
        target=target,
        selection=selection,
        operation="regenerate_asset",
        source_asset_id=source.id,
        source_asset_version=source.version,
        idempotency_key=idempotency_key,
    )


def create_image_candidate_regeneration_task(
    db: Session,
    candidate_id: str,
    *,
    image_provider_profile_id: str | None = None,
    idempotency_key: str | None = None,
) -> GenerationTask:
    source = db.get(AssetCandidate, candidate_id)
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset candidate not found",
        )
    if source.status != "pending_review":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Asset candidate is not pending review",
        )
    existing = _idempotent_task(
        db,
        source.project_id,
        IMAGE_CANDIDATE_TASK_TYPE,
        idempotency_key,
    )
    if existing is not None:
        return existing
    target = prepare_image_regeneration_candidate_target(db, source)
    profile_id = image_provider_profile_id or image_candidate_provider_profile_id(source)
    selection = resolve_image_provider_selection(db, profile_id)
    return _create_single_image_task(
        db,
        target=target,
        selection=selection,
        operation="regenerate_candidate",
        source_candidate_id=source.id,
        source_candidate_version=source.version,
        idempotency_key=idempotency_key,
    )


def _prepare_shot_target(db: Session, shot_id: str) -> ImageCandidateTarget:
    from app.models import Shot

    shot = db.get(Shot, shot_id)
    if shot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shot not found")
    if not shot.is_current:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Current shot is required before storyboard image generation",
        )
    return prepare_shot_image_candidate_targets(
        db,
        shot.project_id,
        shot_id=shot.id,
    )[0]


def _create_single_image_task(
    db: Session,
    *,
    target: ImageCandidateTarget,
    selection: ImageProviderSelection,
    operation: str,
    source_asset_id: str | None = None,
    source_asset_version: int | None = None,
    source_candidate_id: str | None = None,
    source_candidate_version: int | None = None,
    idempotency_key: str | None = None,
) -> GenerationTask:
    repository = TaskRepository()
    try:
        creation = repository.create(
            db,
            project_id=target.project_id,
            task_type=IMAGE_CANDIDATE_TASK_TYPE,
            resource_key=_target_resource_key(target),
            idempotency_key=idempotency_key,
            provider=selection.provider.name,
            model=selection.provider.model,
            input_payload={"operation": operation},
            progress_label="等待图片生成",
        )
        if not creation.created:
            db.commit()
            db.refresh(creation.task)
            return creation.task
        task = creation.task
        task.input_payload = compile_image_candidate_task_input(
            db,
            task_id=task.id,
            project_id=target.project_id,
            entity_type=target.entity_type,
            entity_id=target.entity_id,
            asset_role=target.asset_role,
            variant_key=target.variant_key,
            prompt=target.prompt,
            negative_prompt=target.negative_prompt,
            asset_resolution=target.asset_resolution,
            image_provider_selection=selection,
            operation=operation,
            source_asset_id=source_asset_id,
            source_asset_version=source_asset_version,
            source_candidate_id=source_candidate_id,
            source_candidate_version=source_candidate_version,
        )
        db.add(task)
        mark_stage_running(db, target.project_id, "images", task_id=task.id)
        db.commit()
    except TaskConflictError as exc:
        db.rollback()
        raise _image_conflict(target, exc) from exc
    db.refresh(task)
    return task


def _create_image_batch(
    db: Session,
    *,
    project_id: str,
    parent_task_type: str,
    parent_resource_key: str,
    operation: str,
    targets: list[ImageCandidateTarget],
    selection: ImageProviderSelection,
    idempotency_key: str | None,
) -> GenerationTask:
    dedupe_keys = {
        target.entity_id: active_dedupe_key(
            IMAGE_CANDIDATE_TASK_TYPE,
            _target_resource_key(target),
        )
        for target in targets
    }
    conflicts = list(
        db.scalars(
            select(GenerationTask).where(
                GenerationTask.active_dedupe_key.in_(tuple(dedupe_keys.values()))
            )
        ).all()
    )
    if conflicts:
        _raise_batch_conflict(dedupe_keys, conflicts)

    repository = TaskRepository()
    try:
        parent_creation = repository.create(
            db,
            project_id=project_id,
            task_type=parent_task_type,
            resource_key=parent_resource_key,
            idempotency_key=idempotency_key,
            provider=selection.provider.name,
            model=selection.provider.model,
            input_payload={
                "operation": operation,
                "target_ids": [target.entity_id for target in targets],
                "image_provider_profile_id": selection.profile_id,
            },
            status=TaskStatus.WAITING_CHILDREN,
            progress_label=f"等待子任务（0/{len(targets)}）",
            max_retries=0,
        )
        parent = parent_creation.task
        if not parent_creation.created:
            db.commit()
            db.refresh(parent)
            return parent
        child_ids: list[str] = []
        for target in targets:
            child = repository.create(
                db,
                project_id=project_id,
                task_type=IMAGE_CANDIDATE_TASK_TYPE,
                parent_task_id=parent.id,
                resource_key=_target_resource_key(target),
                provider=selection.provider.name,
                model=selection.provider.model,
                input_payload={"operation": operation},
                progress_label=f"等待生成：{target.label}",
            ).task
            child.input_payload = compile_image_candidate_task_input(
                db,
                task_id=child.id,
                project_id=project_id,
                entity_type=target.entity_type,
                entity_id=target.entity_id,
                asset_role=target.asset_role,
                variant_key=target.variant_key,
                prompt=target.prompt,
                negative_prompt=target.negative_prompt,
                asset_resolution=target.asset_resolution,
                image_provider_selection=selection,
                operation=operation,
            )
            db.add(child)
            child_ids.append(child.id)
        parent.result_payload = {
            "child_task_ids": child_ids,
            "summary": {"total": len(child_ids), "queued": len(child_ids)},
        }
        db.add(parent)
        mark_stage_running(db, project_id, "images", task_id=parent.id)
        db.commit()
    except TaskConflictError as exc:
        db.rollback()
        conflicts = list(
            db.scalars(
                select(GenerationTask).where(
                    GenerationTask.active_dedupe_key.in_(tuple(dedupe_keys.values()))
                )
            ).all()
        )
        if conflicts:
            _raise_batch_conflict(dedupe_keys, conflicts)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "图片批次与活动任务冲突", "task_id": exc.task_id},
        ) from exc
    db.refresh(parent)
    return parent


def _target_resource_key(target: ImageCandidateTarget) -> str:
    variant = target.variant_key or "base"
    return (
        f"{target.entity_type}:{target.entity_id}:image:"
        f"{target.asset_role}:{variant}"
    )


def _idempotent_task(
    db: Session,
    project_id: str,
    task_type: str,
    idempotency_key: str | None,
) -> GenerationTask | None:
    normalized = (idempotency_key or "").strip()
    if not normalized:
        return None
    return db.scalar(
        select(GenerationTask)
        .where(GenerationTask.project_id == project_id)
        .where(GenerationTask.task_type == task_type)
        .where(GenerationTask.idempotency_key == normalized)
    )


def _image_conflict(
    target: ImageCandidateTarget,
    exc: TaskConflictError,
) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "resource_generation_in_progress",
            "message": f"{target.label} 已有活动图片任务",
            "task_id": exc.task_id,
            "conflict_target_ids": [target.entity_id],
        },
    )


def _raise_batch_conflict(
    dedupe_keys: dict[str, str],
    tasks: list[GenerationTask],
) -> None:
    active_keys = {task.active_dedupe_key for task in tasks}
    target_ids = [
        target_id
        for target_id, dedupe_key in dedupe_keys.items()
        if dedupe_key in active_keys
    ]
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "image_batch_conflict",
            "message": "一个或多个图片目标已有活动任务，批次未创建",
            "conflict_target_ids": target_ids,
            "conflict_task_ids": [task.id for task in tasks],
        },
    )
