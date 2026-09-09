"""Retired synchronous video use cases isolated inside production.

The active task runtime uses the focused compiler, executor, persistence, and
batch modules. These functions remain only for compatibility tests and local
maintenance commands.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agents import shot_video_prompt
from app.core.config import settings
from app.models import (
    Asset,
    AssetCandidate,
    GenerationTask,
    Shot,
)
from app.providers.defaults import provider_registry
from app.providers.types import ProviderRequest, ProviderResponse, ProviderStatus, ProviderType
from app.production.video_candidate_persistence import persist_provider_video_candidate
from app.production.video_request_compiler import (
    decimal_or_none,
    duration_request_metadata,
    project_prompt_context,
    public_provider_capabilities,
    reference_asset_ids,
    resolve_video_duration_request,
    shot_card_with_duration_mode,
    shot_card_with_video_prompt,
    shot_entities_from_context,
    shot_video_provider_params,
)
from app.services.artifact_idempotency import artifact_completion_key
from app.services.asset_lifecycle_service import apply_actual_video_duration_to_shot
from app.services.asset_repository import (
    asset_source_context,
    get_project_or_404,
    infer_asset_role,
    next_asset_version,
    next_candidate_version,
    resolve_shot_video_input_asset,
    resolve_source_stage_run_id,
)
from app.services.asset_resolver_service import resolve_generation_assets
from app.services.failure_reason_service import normalize_failure_reason
from app.services.media_generation_support import (
    batch_progress,
    create_generation_task,
    extract_task_error,
    finish_candidate_generation_task,
    finish_generation_task,
    sanitize_large_payload,
    with_avd_asset_usage,
)
from app.services.media_storage_service import materialize_data_uri
from app.services.task_service import mark_task_failed, mark_task_running, update_task_progress
from app.services.workflow_state_service import (
    mark_downstream_stages_pending,
    mark_stage_approved,
    mark_stage_failed,
    mark_stage_ready,
    mark_stage_running,
)


def generate_shot_videos(db: Session, project_id: str) -> tuple[list[Asset], GenerationTask]:
    get_project_or_404(db, project_id)
    shots = list(
        db.scalars(
            select(Shot)
            .where(Shot.project_id == project_id)
            .where(Shot.is_current.is_(True))
            .order_by(Shot.shot_no),
        ).all()
    )
    if not shots:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Video segments are required before generation",
        )

    video_input_images = {
        shot.id: resolve_shot_video_input_asset(db, shot)
        for shot in shots
    }

    asset_resolutions = [
        resolve_generation_assets(
            db,
            project_id,
            stage="shot_video",
            shot=shot,
            fallback_image_asset=video_input_images[shot.id],
        ).to_task_payload()
        for shot in shots
    ]
    video_provider = provider_registry.get(ProviderType.VIDEO, settings.video_provider)
    prompt_context = project_prompt_context(db, project_id)
    task = create_generation_task(
        db,
        project_id=project_id,
        task_type="shot_video_generation",
        input_payload={
            "shot_ids": [shot.id for shot in shots],
            "image_asset_ids": [
                image_asset.id
                for shot in shots
                if (image_asset := video_input_images[shot.id]) is not None
            ],
            "asset_resolutions": asset_resolutions,
        },
        provider=video_provider.name,
        model=video_provider.model,
    )
    mark_stage_running(db, project_id, "videos", task_id=task.id)
    assets = []
    try:
        for index, shot in enumerate(shots, start=1):
            update_task_progress(db, task, f"正在生成视频片段：镜头 {shot.shot_no}", batch_progress(index - 1, len(shots)))
            assets.append(
                _generate_video_asset(
                    db,
                    project_id=project_id,
                    task_id=task.id,
                    shot=shot,
                    image_asset=video_input_images[shot.id],
                    prompt_context=prompt_context,
                )
            )
    except Exception as exc:
        code, message, raw_response = extract_task_error(exc)
        failure_code = code or "shot_video_generation_failed"
        mark_task_failed(db, task, code=failure_code, message=message, raw_response=raw_response)
        mark_stage_failed(
            db,
            project_id,
            "videos",
            summary=message,
            task_id=task.id,
            error_code=failure_code,
            failure_reason=normalize_failure_reason(code=failure_code, message=message, raw_response=raw_response, stage="videos"),
        )
        db.commit()
        raise
    mark_stage_ready(
        db,
        project_id,
        "videos",
        task_id=task.id,
        summary=f"{len(assets)} 个视频片段候选已生成，等待采用",
        metadata={"asset_count": len(assets)},
    )
    mark_downstream_stages_pending(db, project_id, "videos", summary="视频片段已更新，需要重新导出")
    finish_generation_task(db, task, assets)
    return assets, task


def generate_shot_video_candidates(db: Session, project_id: str) -> tuple[list[AssetCandidate], GenerationTask]:
    get_project_or_404(db, project_id)
    shots = list(
        db.scalars(
            select(Shot)
            .where(Shot.project_id == project_id)
            .where(Shot.is_current.is_(True))
            .order_by(Shot.shot_no),
        ).all()
    )
    if not shots:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Video segments are required before generation",
        )

    video_input_images = {
        shot.id: resolve_shot_video_input_asset(db, shot)
        for shot in shots
    }

    asset_resolutions = [
        resolve_generation_assets(
            db,
            project_id,
            stage="shot_video",
            shot=shot,
            fallback_image_asset=video_input_images[shot.id],
        ).to_task_payload()
        for shot in shots
    ]
    video_provider = provider_registry.get(ProviderType.VIDEO, settings.video_provider)
    prompt_context = project_prompt_context(db, project_id)
    task = create_generation_task(
        db,
        project_id=project_id,
        task_type="shot_video_candidate_generation",
        input_payload={
            "shot_ids": [shot.id for shot in shots],
            "image_asset_ids": [
                image_asset.id
                for shot in shots
                if (image_asset := video_input_images[shot.id]) is not None
            ],
            "asset_resolutions": asset_resolutions,
            "candidate_policy": "pending_review_before_asset",
        },
        provider=video_provider.name,
        model=video_provider.model,
    )
    mark_stage_running(db, project_id, "videos", task_id=task.id)
    # Persist the running task before the first long remote inference. Holding
    # one SQLite write transaction across an entire multi-shot batch makes the
    # task invisible, blocks unrelated writes, and loses every completed
    # candidate if the API process exits near the end of the batch.
    db.commit()
    db.refresh(task)
    candidates: list[AssetCandidate] = []
    try:
        for index, shot in enumerate(shots, start=1):
            update_task_progress(db, task, f"正在生成视频片段候选：镜头 {shot.shot_no}", batch_progress(index - 1, len(shots)))
            db.commit()
            candidate = _generate_video_candidate(
                db,
                project_id=project_id,
                task_id=task.id,
                shot=shot,
                image_asset=video_input_images[shot.id],
                prompt_context=prompt_context,
            )
            candidates.append(candidate)
            update_task_progress(
                db,
                task,
                f"视频片段候选已生成：镜头 {shot.shot_no}",
                batch_progress(index, len(shots)),
            )
            db.commit()
            db.refresh(candidate)
            db.refresh(task)
    except Exception as exc:
        code, message, raw_response = extract_task_error(exc)
        failure_code = code or "shot_video_candidate_generation_failed"
        mark_task_failed(db, task, code=failure_code, message=message, raw_response=raw_response)
        mark_stage_failed(
            db,
            project_id,
            "videos",
            summary=message,
            task_id=task.id,
            error_code=failure_code,
            failure_reason=normalize_failure_reason(code=failure_code, message=message, raw_response=raw_response, stage="videos"),
        )
        db.commit()
        raise
    mark_stage_ready(
        db,
        project_id,
        "videos",
        task_id=task.id,
        summary=f"{len(candidates)} 个视频片段候选已生成，等待确认入库",
        metadata={"candidate_count": len(candidates), "kind": "shot_video_candidates"},
    )
    finish_candidate_generation_task(db, task, candidates)
    return candidates, task


def generate_single_shot_video(
    db: Session,
    shot_id: str,
    *,
    duration_mode: str | None = None,
    duration_sec: Decimal | None = None,
    video_prompt: str | None = None,
) -> tuple[Asset, GenerationTask]:
    shot = db.get(Shot, shot_id)
    if shot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shot not found")

    if not shot.is_current:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Current shot is required before single shot video generation",
        )

    video_provider = provider_registry.get(ProviderType.VIDEO, settings.video_provider)
    resolved_duration_mode, resolved_duration = resolve_video_duration_request(
        video_provider,
        shot,
        duration_mode=duration_mode,
        duration_sec=duration_sec,
    )
    if resolved_duration_mode == "fixed" and resolved_duration is not None:
        shot.duration_sec = resolved_duration
    shot.shot_card = shot_card_with_duration_mode(
        shot.shot_card,
        resolved_duration_mode,
        planned_duration_sec=resolved_duration if resolved_duration_mode == "fixed" else None,
        duration_source="manual" if duration_mode == "fixed" or duration_sec is not None else None,
    )
    if video_prompt is not None:
        shot.video_prompt = video_prompt.strip() or None
        shot.shot_card = shot_card_with_video_prompt(shot.shot_card, shot.video_prompt)
    db.add(shot)
    db.flush()

    image_asset = resolve_shot_video_input_asset(db, shot)

    asset_resolution = resolve_generation_assets(
        db,
        shot.project_id,
        stage="shot_video",
        shot=shot,
        fallback_image_asset=image_asset,
    )
    task = create_generation_task(
        db,
        project_id=shot.project_id,
        task_type="single_shot_video_generation",
        input_payload={
            "shot_id": shot.id,
            "shot_no": shot.shot_no,
            "duration_mode": resolved_duration_mode,
            "duration_sec": str(resolved_duration) if resolved_duration is not None else None,
            "video_prompt": shot.video_prompt,
            "image_asset_id": image_asset.id if image_asset else None,
            "video_reference_source": "manual" if image_asset else "none",
            "asset_resolution": asset_resolution.to_task_payload(),
            "retry_policy": "manual_regeneration",
        },
        provider=video_provider.name,
        model=video_provider.model,
    )
    mark_stage_running(db, shot.project_id, "videos", task_id=task.id)
    mark_task_running(db, task, f"正在生成单个视频片段：镜头 {shot.shot_no}", 20)
    try:
        asset = _generate_video_asset(
            db,
            project_id=shot.project_id,
            task_id=task.id,
            shot=shot,
            image_asset=image_asset,
            prompt_context=project_prompt_context(db, shot.project_id),
            duration_mode=resolved_duration_mode,
            duration_sec=resolved_duration,
        )
    except Exception as exc:
        code, message, raw_response = extract_task_error(exc)
        failure_code = code or "single_shot_video_generation_failed"
        mark_task_failed(db, task, code=failure_code, message=message, raw_response=raw_response)
        mark_stage_failed(
            db,
            shot.project_id,
            "videos",
            summary=message,
            task_id=task.id,
            error_code=failure_code,
            failure_reason=normalize_failure_reason(code=failure_code, message=message, raw_response=raw_response, stage="videos"),
        )
        db.commit()
        raise

    mark_stage_ready(
        db,
        shot.project_id,
        "videos",
        task_id=task.id,
        summary=f"镜头 {shot.shot_no} 视频片段候选已生成，等待采用",
        metadata={"asset_count": 1, "kind": "single_shot_video_generation", "shot_id": shot.id},
    )
    mark_downstream_stages_pending(db, shot.project_id, "videos", summary="视频片段已更新，需要重新导出")
    finish_generation_task(db, task, [asset])
    return asset, task


def generate_single_shot_video_candidate(
    db: Session,
    shot_id: str,
    *,
    duration_mode: str | None = None,
    duration_sec: Decimal | None = None,
    video_prompt: str | None = None,
) -> tuple[AssetCandidate, GenerationTask]:
    task = create_single_shot_video_candidate_task(
        db,
        shot_id,
        duration_mode=duration_mode,
        duration_sec=duration_sec,
        video_prompt=video_prompt,
    )
    result = execute_single_shot_video_candidate_task(db, task.id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Video generation task is no longer queued")
    return result


def create_single_shot_video_candidate_task(
    db: Session,
    shot_id: str,
    *,
    duration_mode: str | None = None,
    duration_sec: Decimal | None = None,
    video_prompt: str | None = None,
) -> GenerationTask:
    shot = db.get(Shot, shot_id)
    if shot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shot not found")

    if not shot.is_current:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Current shot is required before single shot video generation",
        )

    active_tasks = list(
        db.scalars(
            select(GenerationTask)
            .where(GenerationTask.project_id == shot.project_id)
            .where(GenerationTask.task_type == "single_shot_video_candidate_generation")
            .where(GenerationTask.status.in_(("queued", "running")))
        ).all()
    )
    if any((task.input_payload or {}).get("shot_id") == shot.id for task in active_tasks):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"镜头 {shot.shot_no} 已在视频生成队列中",
        )

    video_provider = provider_registry.get(ProviderType.VIDEO, settings.video_provider)
    resolved_duration_mode, resolved_duration = resolve_video_duration_request(
        video_provider,
        shot,
        duration_mode=duration_mode,
        duration_sec=duration_sec,
    )
    if resolved_duration_mode == "fixed" and resolved_duration is not None:
        shot.duration_sec = resolved_duration
    shot.shot_card = shot_card_with_duration_mode(
        shot.shot_card,
        resolved_duration_mode,
        planned_duration_sec=resolved_duration if resolved_duration_mode == "fixed" else None,
        duration_source="manual" if duration_mode == "fixed" or duration_sec is not None else None,
    )
    if video_prompt is not None:
        shot.video_prompt = video_prompt.strip() or None
        shot.shot_card = shot_card_with_video_prompt(shot.shot_card, shot.video_prompt)
    db.add(shot)
    db.flush()

    image_asset = resolve_shot_video_input_asset(db, shot)

    task = GenerationTask(
        project_id=shot.project_id,
        task_type="single_shot_video_candidate_generation",
        input_payload={
            "shot_id": shot.id,
            "shot_no": shot.shot_no,
            "duration_mode": resolved_duration_mode,
            "duration_sec": str(resolved_duration) if resolved_duration is not None else None,
            "video_prompt": shot.video_prompt,
            "image_asset_id": image_asset.id if image_asset else None,
            "video_reference_source": "manual" if image_asset else "none",
            "candidate_policy": "pending_review_before_asset",
        },
        provider=video_provider.name,
        model=video_provider.model,
        status="queued",
        progress=0,
        progress_label="等待生成",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def execute_single_shot_video_candidate_task(
    db: Session,
    task_id: str,
) -> tuple[AssetCandidate, GenerationTask] | None:
    task = db.get(GenerationTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.task_type != "single_shot_video_candidate_generation":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task is not a queued shot video generation")
    if task.status != "queued":
        return None

    shot_id = str((task.input_payload or {}).get("shot_id") or "")
    image_asset_id = str((task.input_payload or {}).get("image_asset_id") or "")
    duration_mode = str((task.input_payload or {}).get("duration_mode") or "") or None
    duration_sec = decimal_or_none((task.input_payload or {}).get("duration_sec"))
    shot = db.get(Shot, shot_id)
    image_asset = db.get(Asset, image_asset_id) if image_asset_id else None
    if shot is None or (image_asset_id and image_asset is None):
        message = "镜头或明确选择的首帧图片已不存在，队列任务无法继续"
        mark_task_failed(db, task, code="queued_video_input_missing", message=message)
        db.commit()
        return None

    mark_task_running(db, task, f"正在生成单个视频候选：镜头 {shot.shot_no}", 20)
    try:
        candidate = _generate_video_candidate(
            db,
            project_id=shot.project_id,
            task_id=task.id,
            shot=shot,
            image_asset=image_asset,
            prompt_context=project_prompt_context(db, shot.project_id),
            duration_mode=duration_mode,
            duration_sec=duration_sec,
        )
    except Exception as exc:
        code, message, raw_response = extract_task_error(exc)
        failure_code = code or "single_shot_video_candidate_generation_failed"
        mark_task_failed(db, task, code=failure_code, message=message, raw_response=raw_response)
        db.commit()
        return None

    from app.services.asset_candidate_service import promote_asset_candidate

    promote_asset_candidate(db, candidate.id, review_note="生成成功后自动采用为当前版本")
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
            metadata={"candidate_count": 1, "kind": "single_shot_video_candidate_generation", "shot_id": shot.id},
        )
    finish_candidate_generation_task(db, task, [candidate])
    return candidate, task


def regenerate_video_asset(db: Session, asset_id: str) -> tuple[Asset, GenerationTask]:
    source_asset = db.get(Asset, asset_id)
    if source_asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    if source_asset.asset_type != "video":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only video assets can be regenerated")
    if source_asset.entity_type != "shot" or not source_asset.entity_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only shot video assets can be regenerated")

    shot = db.get(Shot, source_asset.entity_id)
    if shot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shot not found")

    image_asset = resolve_shot_video_input_asset(db, shot)

    video_provider = provider_registry.get(ProviderType.VIDEO, settings.video_provider)
    asset_resolution = resolve_generation_assets(
        db,
        source_asset.project_id,
        stage="shot_video",
        shot=shot,
        fallback_image_asset=image_asset,
    )
    task = create_generation_task(
        db,
        project_id=source_asset.project_id,
        task_type="shot_video_regeneration",
        input_payload={
            "source_asset_id": source_asset.id,
            "shot_id": shot.id,
            "image_asset_id": image_asset.id if image_asset else None,
            "version": source_asset.version,
            "retry_policy": "manual_regeneration",
            "asset_resolution": asset_resolution.to_task_payload(),
        },
        provider=video_provider.name,
        model=video_provider.model,
    )
    mark_stage_running(db, source_asset.project_id, "videos", task_id=task.id)
    mark_task_running(db, task, f"正在手动重生成视频片段：镜头 {shot.shot_no}", 20)
    try:
        asset = _generate_video_asset(
            db,
            project_id=source_asset.project_id,
            task_id=task.id,
            shot=shot,
            image_asset=image_asset,
        )
    except Exception as exc:
        code, message, raw_response = extract_task_error(exc)
        failure_code = code or "shot_video_regeneration_failed"
        mark_task_failed(db, task, code=failure_code, message=message, raw_response=raw_response)
        mark_stage_failed(
            db,
            source_asset.project_id,
            "videos",
            summary=message,
            task_id=task.id,
            error_code=failure_code,
            failure_reason=normalize_failure_reason(code=failure_code, message=message, raw_response=raw_response, stage="videos"),
        )
        db.commit()
        raise

    mark_stage_ready(
        db,
        source_asset.project_id,
        "videos",
        task_id=task.id,
        summary=f"镜头 {shot.shot_no} 视频片段候选已手动重生成，等待采用",
        metadata={"asset_count": 1, "kind": "shot_video_regeneration", "source_asset_id": source_asset.id},
    )
    mark_downstream_stages_pending(db, source_asset.project_id, "videos", summary="视频片段已更新，需要重新导出")
    finish_generation_task(db, task, [asset])
    return asset, task


def regenerate_video_asset_candidate(db: Session, asset_id: str) -> tuple[AssetCandidate, GenerationTask]:
    source_asset = db.get(Asset, asset_id)
    if source_asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    if source_asset.asset_type != "video":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only video assets can be regenerated")
    if source_asset.entity_type != "shot" or not source_asset.entity_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only shot video assets can be regenerated")

    shot = db.get(Shot, source_asset.entity_id)
    if shot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shot not found")

    image_asset = resolve_shot_video_input_asset(db, shot)

    video_provider = provider_registry.get(ProviderType.VIDEO, settings.video_provider)
    asset_resolution = resolve_generation_assets(
        db,
        source_asset.project_id,
        stage="shot_video",
        shot=shot,
        fallback_image_asset=image_asset,
    )
    task = create_generation_task(
        db,
        project_id=source_asset.project_id,
        task_type="shot_video_regeneration_candidate",
        input_payload={
            "source_asset_id": source_asset.id,
            "shot_id": shot.id,
            "image_asset_id": image_asset.id if image_asset else None,
            "version": source_asset.version,
            "retry_policy": "manual_regeneration",
            "candidate_policy": "pending_review_before_asset",
            "asset_resolution": asset_resolution.to_task_payload(),
        },
        provider=video_provider.name,
        model=video_provider.model,
    )
    mark_stage_running(db, source_asset.project_id, "videos", task_id=task.id)
    mark_task_running(db, task, f"正在手动重生成视频候选：镜头 {shot.shot_no}", 20)
    try:
        candidate = _generate_video_candidate(
            db,
            project_id=source_asset.project_id,
            task_id=task.id,
            shot=shot,
            image_asset=image_asset,
        )
    except Exception as exc:
        code, message, raw_response = extract_task_error(exc)
        failure_code = code or "shot_video_regeneration_candidate_failed"
        mark_task_failed(db, task, code=failure_code, message=message, raw_response=raw_response)
        mark_stage_failed(
            db,
            source_asset.project_id,
            "videos",
            summary=message,
            task_id=task.id,
            error_code=failure_code,
            failure_reason=normalize_failure_reason(code=failure_code, message=message, raw_response=raw_response, stage="videos"),
        )
        db.commit()
        raise

    mark_stage_ready(
        db,
        source_asset.project_id,
        "videos",
        task_id=task.id,
        summary=f"镜头 {shot.shot_no} 视频候选已手动重生成，等待确认入库",
        metadata={"candidate_count": 1, "kind": "shot_video_regeneration_candidate", "source_asset_id": source_asset.id},
    )
    finish_candidate_generation_task(db, task, [candidate])
    return candidate, task


def _generate_video_asset(
    db: Session,
    *,
    project_id: str,
    task_id: str,
    shot: Shot,
    image_asset: Asset | None,
    prompt_context: dict | None = None,
    duration_mode: str | None = None,
    duration_sec: Decimal | None = None,
) -> Asset:
    provider = provider_registry.get(ProviderType.VIDEO, settings.video_provider)
    shot_characters, scene, shot_props = shot_entities_from_context(shot, prompt_context)
    prompt = shot_video_prompt(shot, characters=shot_characters, scene=scene, props=shot_props)
    version = next_asset_version(db, project_id, "video", "shot", shot.id, "shot_video")
    asset_resolution = resolve_generation_assets(
        db,
        project_id,
        stage="shot_video",
        shot=shot,
        fallback_image_asset=image_asset,
    )
    reference_metadata = asset_resolution.reference_metadata
    request_metadata = with_avd_asset_usage(
        {
            "image_asset_id": image_asset.id if image_asset else None,
            "asset_resolution": asset_resolution.to_task_payload(),
            "input_fingerprint": (shot.shot_card or {}).get("prompt_fingerprint"),
            "provider_capabilities": public_provider_capabilities(provider),
        },
        asset_type="video",
        asset_role="shot_video",
        entity_type="shot",
    )
    video_params = shot_video_provider_params(
        shot,
        provider,
        duration_mode=duration_mode,
        duration_sec=duration_sec,
    )
    request_metadata["duration_request"] = duration_request_metadata(video_params)
    _record_video_task_input(
        db,
        task_id=task_id,
        shot=shot,
        prompt=prompt,
        reference_asset_ids=reference_asset_ids(asset_resolution),
        provider=provider,
        video_params=video_params,
    )
    provider_response = provider.submit(
        ProviderRequest(
            project_id=project_id,
            task_id=f"{task_id}:video:shot:{shot.id}:{version}",
            model=provider.model,
            prompt=prompt,
            negative_prompt=shot.negative_prompt,
            references=asset_resolution.references,
            params={
                **video_params,
                "reference_mode": reference_metadata.get("reference_mode", "none"),
            },
            metadata={
                "entity_type": "shot",
                "entity_id": shot.id,
                "image_asset_id": image_asset.id if image_asset else None,
                "asset_resolution": asset_resolution.to_task_payload(),
                "avd_asset_purpose": request_metadata["avd_asset_purpose"],
                "avd_asset_usage": request_metadata["avd_asset_usage"],
                "duration_request": request_metadata["duration_request"],
                **reference_metadata,
            },
        )
    )
    return _persist_provider_video_asset(
        db,
        project_id=project_id,
        entity_type="shot",
        entity_id=shot.id,
        provider_name=provider.name,
        model=provider.model,
        prompt=prompt,
        negative_prompt=shot.negative_prompt,
        response=provider_response,
        source_task_id=task_id,
        request_metadata=request_metadata,
    )


def _generate_video_candidate(
    db: Session,
    *,
    project_id: str,
    task_id: str,
    shot: Shot,
    image_asset: Asset | None,
    prompt_context: dict | None = None,
    duration_mode: str | None = None,
    duration_sec: Decimal | None = None,
) -> AssetCandidate:
    provider = provider_registry.get(ProviderType.VIDEO, settings.video_provider)
    shot_characters, scene, shot_props = shot_entities_from_context(shot, prompt_context)
    prompt = shot_video_prompt(shot, characters=shot_characters, scene=scene, props=shot_props)
    version = next_candidate_version(db, project_id, "video", "shot", shot.id, "shot_video")
    asset_resolution = resolve_generation_assets(
        db,
        project_id,
        stage="shot_video",
        shot=shot,
        fallback_image_asset=image_asset,
    )
    reference_metadata = asset_resolution.reference_metadata
    request_metadata = with_avd_asset_usage(
        {
            "image_asset_id": image_asset.id if image_asset else None,
            "asset_resolution": asset_resolution.to_task_payload(),
            "candidate_policy": "pending_review_before_asset",
            "input_fingerprint": (shot.shot_card or {}).get("prompt_fingerprint"),
            "provider_capabilities": public_provider_capabilities(provider),
        },
        asset_type="video",
        asset_role="shot_video",
        entity_type="shot",
    )
    video_params = shot_video_provider_params(
        shot,
        provider,
        duration_mode=duration_mode,
        duration_sec=duration_sec,
    )
    request_metadata["duration_request"] = duration_request_metadata(video_params)
    _record_video_task_input(
        db,
        task_id=task_id,
        shot=shot,
        prompt=prompt,
        reference_asset_ids=reference_asset_ids(asset_resolution),
        provider=provider,
        video_params=video_params,
    )
    provider_response = provider.submit(
        ProviderRequest(
            project_id=project_id,
            task_id=f"{task_id}:video-candidate:shot:{shot.id}:{version}",
            model=provider.model,
            prompt=prompt,
            negative_prompt=shot.negative_prompt,
            references=asset_resolution.references,
            params={
                **video_params,
                "reference_mode": reference_metadata.get("reference_mode", "none"),
            },
            metadata={
                "entity_type": "shot",
                "entity_id": shot.id,
                "image_asset_id": image_asset.id if image_asset else None,
                "asset_resolution": asset_resolution.to_task_payload(),
                "candidate_policy": "pending_review_before_asset",
                "avd_asset_purpose": request_metadata["avd_asset_purpose"],
                "avd_asset_usage": request_metadata["avd_asset_usage"],
                "duration_request": request_metadata["duration_request"],
                **reference_metadata,
            },
        )
    )
    return persist_provider_video_candidate(
        db,
        project_id=project_id,
        entity_type="shot",
        entity_id=shot.id,
        provider_name=provider.name,
        model=provider.model,
        prompt=prompt,
        negative_prompt=shot.negative_prompt,
        response=provider_response,
        source_task_id=task_id,
        request_metadata=request_metadata,
    )


def _persist_provider_video_asset(
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
) -> Asset:
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
    resolved_asset_role = infer_asset_role(asset_type="video", entity_type=entity_type, entity_id=entity_id)
    request_metadata = with_avd_asset_usage(
        request_metadata,
        asset_type="video",
        asset_role=resolved_asset_role,
        entity_type=entity_type,
    )
    version = next_asset_version(db, project_id, "video", entity_type, entity_id, resolved_asset_role)
    source_script_id, source_shot_batch_id = asset_source_context(db, entity_type, entity_id)
    source_stage_run_id = resolve_source_stage_run_id(db, project_id, source_task_id)

    db.execute(
        update(Asset)
        .where(Asset.project_id == project_id)
        .where(Asset.asset_type == "video")
        .where(Asset.asset_role == resolved_asset_role)
        .where(Asset.entity_type == entity_type)
        .where(Asset.entity_id == entity_id)
        .values(is_selected=False),
    )
    asset = Asset(
        project_id=project_id,
        asset_type="video",
        asset_role=resolved_asset_role,
        entity_type=entity_type,
        entity_id=entity_id,
        variant_key="base" if entity_type in {"character", "scene", "prop"} else None,
        source_task_id=source_task_id,
        completion_key=artifact_completion_key(
            source_task_id,
            artifact_kind="asset",
            asset_type="video",
            asset_role=resolved_asset_role,
            entity_type=entity_type,
            entity_id=entity_id,
            variant_key="base" if entity_type in {"character", "scene", "prop"} else None,
        ),
        source_stage_run_id=source_stage_run_id,
        source_script_id=source_script_id,
        source_shot_batch_id=source_shot_batch_id,
        version=version,
        uri=materialize_data_uri(project_id, f"{uuid4()}", provider_asset.uri, provider_asset.mime_type),
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
        status="ready_for_review",
        is_selected=True,
    )
    apply_actual_video_duration_to_shot(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        duration_sec=provider_asset.duration_sec,
        request_metadata=request_metadata,
    )
    db.add(asset)
    db.flush()
    return asset


def _record_video_task_input(
    db: Session,
    *,
    task_id: str,
    shot: Shot,
    prompt: str,
    reference_asset_ids: list[str],
    provider: Any,
    video_params: dict[str, Any],
) -> None:
    task = db.get(GenerationTask, task_id)
    if task is None:
        return
    payload = dict(task.input_payload or {})
    audits = [
        item
        for item in list(payload.get("shot_prompt_audits") or [])
        if isinstance(item, dict) and item.get("shot_id") != shot.id
    ]
    audits.append(
        {
            "shot_id": shot.id,
            "final_prompt": prompt,
            "input_fingerprint": (shot.shot_card or {}).get("prompt_fingerprint"),
            "reference_asset_ids": list(dict.fromkeys(reference_asset_ids)),
            "provider": provider.name,
            "model": provider.model,
            "duration_request": duration_request_metadata(video_params),
            "provider_capabilities": public_provider_capabilities(provider),
        }
    )
    payload["shot_prompt_audits"] = audits
    task.input_payload = payload
    db.add(task)
    db.flush()
