"""Video generation orchestration, queue execution, and duration policy."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agents import shot_video_params, shot_video_prompt
from app.core.config import settings
from app.models import (
    Asset,
    AssetCandidate,
    Character,
    GenerationTask,
    Prop,
    Scene,
    Shot,
)
from app.providers.defaults import provider_registry
from app.providers.types import ProviderRequest, ProviderResponse, ProviderStatus, ProviderType
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
from app.services.entity_service import get_current_entities
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
from app.services.task_service import mark_task_failed, mark_task_running, mark_task_succeeded, update_task_progress
from app.services.workflow_state_service import (
    mark_downstream_stages_pending,
    mark_stage_approved,
    mark_stage_failed,
    mark_stage_ready,
    mark_stage_running,
)


VIDEO_CANDIDATE_TASK_TYPE = "shot_video_candidate_generation"


def compile_video_candidate_task_input(
    db: Session,
    shot: Shot,
    *,
    duration_mode: str | None = None,
    duration_sec: Decimal | None = None,
    video_prompt: str | None = None,
    source_asset: Asset | None = None,
    update_shot: bool = False,
    provider_name: str | None = None,
    model: str | None = None,
) -> tuple[dict[str, Any], Any]:
    """Freeze every provider-relevant value before the task is queued."""
    provider = provider_registry.get(
        ProviderType.VIDEO,
        provider_name or settings.video_provider,
    )
    resolved_model = model or provider.model
    resolved_duration_mode, resolved_duration = _resolve_video_duration_request(
        provider,
        shot,
        duration_mode=duration_mode,
        duration_sec=duration_sec,
    )
    if update_shot:
        if resolved_duration_mode == "fixed" and resolved_duration is not None:
            shot.duration_sec = resolved_duration
        shot.shot_card = _shot_card_with_duration_mode(shot.shot_card, resolved_duration_mode)
        if video_prompt is not None:
            shot.video_prompt = video_prompt.strip() or None
            shot.shot_card = _shot_card_with_video_prompt(shot.shot_card, shot.video_prompt)
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
    shot_characters, scene, shot_props = _shot_entities_from_context(
        shot,
        _project_prompt_context(db, shot.project_id),
    )
    prompt = shot_video_prompt(shot, characters=shot_characters, scene=scene, props=shot_props)
    video_params = _shot_video_provider_params(
        shot,
        provider,
        duration_mode=resolved_duration_mode,
        duration_sec=resolved_duration,
    )
    reference_metadata = asset_resolution.reference_metadata
    request_metadata = with_avd_asset_usage(
        {
            "image_asset_id": image_asset.id if image_asset else None,
            "asset_resolution": asset_resolution.to_task_payload(),
            "candidate_policy": "pending_review_before_asset",
            "input_fingerprint": (shot.shot_card or {}).get("prompt_fingerprint"),
            "provider_capabilities": _public_provider_capabilities(provider),
            "duration_request": _duration_request_metadata(video_params),
        },
        asset_type="video",
        asset_role="shot_video",
        entity_type="shot",
    )
    payload: dict[str, Any] = {
        "shot_id": shot.id,
        "shot_no": shot.shot_no,
        "duration_mode": resolved_duration_mode,
        "duration_sec": str(resolved_duration) if resolved_duration is not None else None,
        "video_prompt": shot.video_prompt,
        "image_asset_id": image_asset.id if image_asset else None,
        "video_reference_source": "manual" if image_asset else "none",
        "candidate_policy": "pending_review_before_asset",
        "asset_resolution": asset_resolution.to_task_payload(),
        "source_asset_id": source_asset.id if source_asset else None,
        "source_asset_version": source_asset.version if source_asset else None,
        "provider_request": {
            "model": resolved_model,
            "prompt": prompt,
            "negative_prompt": shot.negative_prompt,
            "references": list(asset_resolution.references),
            "params": {
                **video_params,
                "reference_mode": reference_metadata.get("reference_mode", "none"),
            },
            "metadata": {
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
        },
        "request_metadata": request_metadata,
        "shot_prompt_audits": [
            {
                "shot_id": shot.id,
                "final_prompt": prompt,
                "input_fingerprint": (shot.shot_card or {}).get("prompt_fingerprint"),
                "reference_asset_ids": _reference_asset_ids(asset_resolution),
                "provider": provider.name,
                "model": resolved_model,
                "duration_request": _duration_request_metadata(video_params),
                "provider_capabilities": _public_provider_capabilities(provider),
            }
        ],
    }
    return payload, provider


def persist_video_candidate_task_result(
    db: Session,
    task: GenerationTask,
    response: ProviderResponse,
) -> AssetCandidate:
    """Persist and adopt a candidate from a frozen task snapshot."""
    payload = task.input_payload if isinstance(task.input_payload, dict) else {}
    request_payload = payload.get("provider_request")
    if not isinstance(request_payload, dict):
        raise ValueError("Video task provider request snapshot is missing")
    shot_id = str(payload.get("shot_id") or "")
    shot = db.get(Shot, shot_id)
    if shot is None:
        raise ValueError("Video task shot no longer exists")
    candidate = _persist_provider_video_candidate(
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

    from app.services.asset_candidate_service import promote_asset_candidate

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
            metadata={"candidate_count": 1, "kind": task.task_type, "shot_id": shot.id},
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
    prompt_context = _project_prompt_context(db, project_id)
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
    prompt_context = _project_prompt_context(db, project_id)
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
    resolved_duration_mode, resolved_duration = _resolve_video_duration_request(
        video_provider,
        shot,
        duration_mode=duration_mode,
        duration_sec=duration_sec,
    )
    if resolved_duration_mode == "fixed" and resolved_duration is not None:
        shot.duration_sec = resolved_duration
    shot.shot_card = _shot_card_with_duration_mode(shot.shot_card, resolved_duration_mode)
    if video_prompt is not None:
        shot.video_prompt = video_prompt.strip() or None
        shot.shot_card = _shot_card_with_video_prompt(shot.shot_card, shot.video_prompt)
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
            prompt_context=_project_prompt_context(db, shot.project_id),
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
    resolved_duration_mode, resolved_duration = _resolve_video_duration_request(
        video_provider,
        shot,
        duration_mode=duration_mode,
        duration_sec=duration_sec,
    )
    if resolved_duration_mode == "fixed" and resolved_duration is not None:
        shot.duration_sec = resolved_duration
    shot.shot_card = _shot_card_with_duration_mode(shot.shot_card, resolved_duration_mode)
    if video_prompt is not None:
        shot.video_prompt = video_prompt.strip() or None
        shot.shot_card = _shot_card_with_video_prompt(shot.shot_card, shot.video_prompt)
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
    duration_sec = _decimal_or_none((task.input_payload or {}).get("duration_sec"))
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
            prompt_context=_project_prompt_context(db, shot.project_id),
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
    shot_characters, scene, shot_props = _shot_entities_from_context(shot, prompt_context)
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
            "provider_capabilities": _public_provider_capabilities(provider),
        },
        asset_type="video",
        asset_role="shot_video",
        entity_type="shot",
    )
    video_params = _shot_video_provider_params(
        shot,
        provider,
        duration_mode=duration_mode,
        duration_sec=duration_sec,
    )
    request_metadata["duration_request"] = _duration_request_metadata(video_params)
    _record_video_task_input(
        db,
        task_id=task_id,
        shot=shot,
        prompt=prompt,
        reference_asset_ids=_reference_asset_ids(asset_resolution),
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
    shot_characters, scene, shot_props = _shot_entities_from_context(shot, prompt_context)
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
            "provider_capabilities": _public_provider_capabilities(provider),
        },
        asset_type="video",
        asset_role="shot_video",
        entity_type="shot",
    )
    video_params = _shot_video_provider_params(
        shot,
        provider,
        duration_mode=duration_mode,
        duration_sec=duration_sec,
    )
    request_metadata["duration_request"] = _duration_request_metadata(video_params)
    _record_video_task_input(
        db,
        task_id=task_id,
        shot=shot,
        prompt=prompt,
        reference_asset_ids=_reference_asset_ids(asset_resolution),
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
    return _persist_provider_video_candidate(
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


def _shot_video_provider_params(
    shot: Shot,
    provider: Any,
    *,
    duration_mode: str | None = None,
    duration_sec: Decimal | None = None,
) -> dict:
    resolved_mode, resolved_duration = _resolve_video_duration_request(
        provider,
        shot,
        duration_mode=duration_mode,
        duration_sec=duration_sec,
    )
    provider_name = str(getattr(provider, "name", ""))
    if provider_name == "seedance2_api":
        return {
            "duration_mode": resolved_mode,
            "duration": -1 if resolved_mode == "provider_auto" else float(resolved_duration),
            "ratio": str(getattr(shot.project, "aspect_ratio", None) or settings.seedance2_ratio),
            "resolution": settings.seedance2_resolution,
            "generate_audio": settings.seedance2_generate_audio,
            "watermark": settings.seedance2_watermark,
        }
    if provider_name == "mock":
        params = {
            **shot_video_params(
                shot,
                fps=settings.wan_i2v_fps,
                max_frames=settings.wan_i2v_frames,
                default_steps=settings.wan_i2v_steps,
                default_guidance=settings.wan_i2v_guidance,
            ),
            "ratio": str(getattr(shot.project, "aspect_ratio", None) or "16:9"),
            "resolution": str(getattr(shot.project, "resolution", None) or "854x480"),
        }
    elif provider_name == "ltx23_api":
        params = shot_video_params(
            shot,
            fps=max(1, round(settings.ltx23_fps)),
            max_frames=settings.ltx23_frames,
            default_steps=settings.ltx23_steps,
            default_guidance=settings.ltx23_cfg,
        )
    else:
        params = shot_video_params(
            shot,
            fps=settings.wan_i2v_fps,
            max_frames=settings.wan_i2v_frames,
            default_steps=settings.wan_i2v_steps,
            default_guidance=settings.wan_i2v_guidance,
        )
    if resolved_duration is not None:
        params["duration_sec"] = float(resolved_duration)
    params["duration_mode"] = resolved_mode
    _assert_video_provider_duration(
        provider,
        params.get("duration_sec"),
        shot_no=shot.shot_no,
    )
    return params


def _resolve_video_duration_request(
    provider: Any,
    shot: Shot,
    *,
    duration_mode: str | None,
    duration_sec: Decimal | None,
) -> tuple[str, Decimal | None]:
    mode = str(duration_mode or "").strip()
    if mode and mode not in {"provider_auto", "fixed"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported video duration mode",
        )
    if not mode:
        if duration_sec is not None:
            mode = "fixed"
        elif bool(getattr(provider, "smart_duration", False)):
            mode = "provider_auto"
        else:
            mode = "fixed"

    if mode == "provider_auto":
        if duration_sec is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Smart duration cannot include duration_sec",
            )
        if not bool(getattr(provider, "smart_duration", False)):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "video_smart_duration_unsupported",
                    "message": "当前视频模型不支持智能时长，请使用固定时长。",
                    "provider": str(getattr(provider, "name", "video_provider")),
                    "model": str(getattr(provider, "model", "")),
                },
            )
        return mode, None

    resolved_duration = duration_sec if duration_sec is not None else shot.duration_sec
    if duration_mode == "fixed" and resolved_duration is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Fixed duration requires duration_sec",
        )
    if resolved_duration is not None:
        _assert_video_provider_duration(
            provider,
            resolved_duration,
            shot_no=shot.shot_no,
        )
    return mode, resolved_duration


def _duration_request_metadata(video_params: dict[str, Any]) -> dict[str, Any]:
    mode = str(video_params.get("duration_mode") or "fixed")
    requested = video_params.get("duration")
    if requested is None:
        requested = video_params.get("duration_sec")
    return {
        "mode": mode,
        "provider_value": requested,
        "requested_duration_sec": None if mode == "provider_auto" else requested,
    }


def _public_provider_capabilities(provider: Any) -> dict[str, Any]:
    descriptor = provider.descriptor()
    return {
        key: descriptor.get(key)
        for key in (
            "native_audio",
            "reference_images",
            "reference_videos",
            "reference_audio",
            "multi_reference",
            "smart_duration",
            "min_duration_sec",
            "max_duration_sec",
            "supported_resolutions",
        )
    }


def _assert_video_provider_duration(
    provider: Any,
    duration_sec: object,
    *,
    shot_no: int | None = None,
) -> None:
    min_duration = getattr(provider, "min_duration_sec", None)
    max_duration = getattr(provider, "max_duration_sec", None)
    if min_duration is None and max_duration is None:
        return
    try:
        duration = float(duration_sec)
    except (TypeError, ValueError):
        return
    supported_min = float(min_duration) if min_duration is not None else None
    supported_max = float(max_duration) if max_duration is not None else None
    if (
        (supported_min is None or duration >= supported_min)
        and (supported_max is None or duration <= supported_max)
    ):
        return
    provider_name = str(getattr(provider, "name", "video_provider"))
    model = str(getattr(provider, "model", ""))
    shot_label = f"片段 {shot_no}" if shot_no is not None else "当前片段"
    range_text = (
        f"{supported_min:g}-{supported_max:g} 秒"
        if supported_min is not None and supported_max is not None
        else f"至少 {supported_min:g} 秒"
        if supported_min is not None
        else f"最多 {supported_max:g} 秒"
    )
    error_code = (
        "video_duration_below_provider_minimum"
        if supported_min is not None and duration < supported_min
        else "video_duration_exceeds_provider_limit"
    )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": error_code,
            "message": (
                f"{shot_label} 的固定时长为 {duration:g} 秒，不符合当前视频模型 "
                f"{model or provider_name} 支持的 {range_text}；"
                "请使用智能时长或调整固定值"
            ),
            "provider": provider_name,
            "model": model,
            "shot_duration_sec": duration,
            "min_duration_sec": supported_min,
            "max_duration_sec": supported_max,
        },
    )


def _reference_asset_ids(asset_resolution: Any) -> list[str]:
    metadata = (
        asset_resolution.reference_metadata
        if isinstance(getattr(asset_resolution, "reference_metadata", None), dict)
        else {}
    )
    values = metadata.get("reference_asset_ids")
    if not isinstance(values, list):
        return []
    return [str(item) for item in values if str(item).strip()]


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
            "duration_request": _duration_request_metadata(video_params),
            "provider_capabilities": _public_provider_capabilities(provider),
        }
    )
    payload["shot_prompt_audits"] = audits
    task.input_payload = payload
    db.add(task)
    db.flush()


def _persist_provider_video_candidate(
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
    resolved_asset_role = infer_asset_role(asset_type="video", entity_type=entity_type, entity_id=entity_id)
    request_metadata = with_avd_asset_usage(
        request_metadata,
        asset_type="video",
        asset_role=resolved_asset_role,
        entity_type=entity_type,
    )
    version = next_candidate_version(db, project_id, "video", entity_type, entity_id, resolved_asset_role)
    source_script_id, source_shot_batch_id = asset_source_context(db, entity_type, entity_id)
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
        source_stage_run_id=source_stage_run_id,
        source_script_id=source_script_id,
        source_shot_batch_id=source_shot_batch_id,
        candidate_type="generated",
        version=version,
        uri=materialize_data_uri(project_id, candidate_id, provider_asset.uri, provider_asset.mime_type),
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


def _shot_card_with_video_prompt(card: object, video_prompt: str | None) -> dict:
    updated = dict(card) if isinstance(card, dict) else {}
    if not video_prompt:
        return updated
    updated["video_prompt_override"] = {
        "value": video_prompt,
        "scope": "single_shot_generation",
        "note": "Do not copy this full prompt into action or frame_plan fields.",
    }
    return updated


def _shot_card_with_duration_mode(card: object, duration_mode: str) -> dict:
    updated = dict(card) if isinstance(card, dict) else {}
    segment_plan = (
        dict(updated.get("segment_plan"))
        if isinstance(updated.get("segment_plan"), dict)
        else {}
    )
    segment_plan["duration_mode"] = duration_mode
    updated["segment_plan"] = segment_plan
    return updated


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return None


def _project_prompt_context(db: Session, project_id: str) -> dict:
    characters, scenes, props = get_current_entities(db, project_id)
    return {
        "characters": {item.id: item for item in characters},
        "scenes": {item.id: item for item in scenes},
        "props": {item.id: item for item in props},
    }


def _shot_entities_from_context(shot: Shot, prompt_context: dict | None) -> tuple[list[Character], Scene | None, list[Prop]]:
    prompt_context = prompt_context or {}
    character_map = prompt_context.get("characters") or {}
    scene_map = prompt_context.get("scenes") or {}
    prop_map = prompt_context.get("props") or {}
    characters = [
        character_map[item]
        for item in (shot.character_ids or [])
        if isinstance(item, str) and item in character_map
    ]
    scene = scene_map.get(shot.scene_id) if shot.scene_id else None
    props = [
        prop_map[item]
        for item in (shot.prop_ids or [])
        if isinstance(item, str) and item in prop_map
    ]
    return characters, scene, props
