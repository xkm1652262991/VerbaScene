"""Image generation orchestration and persistence."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import blake2b
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agents import (
    character_reference_prompt,
    prop_reference_prompt,
    reference_negative_prompt,
    scene_reference_prompt,
)
from app.agents.camera_language import professional_movement, professional_shot_size
from app.agents.entity_design import asset_spec_reference_required
from app.agents.style import compose_image_prompt, resolve_visual_style
from app.core.config import settings
from app.models import (
    Asset,
    AssetCandidate,
    Character,
    GenerationTask,
    Project,
    Prop,
    Scene,
    Shot,
)
from app.providers.defaults import provider_registry
from app.providers.types import ProviderRequest, ProviderResponse, ProviderStatus, ProviderType
from app.services.artifact_idempotency import artifact_completion_key
from app.services.asset_resolver_service import AssetResolution, resolve_generation_assets
from app.services.asset_repository import (
    asset_source_context,
    get_project_or_404,
    infer_asset_role,
    next_asset_version,
    next_candidate_version,
    resolve_source_stage_run_id,
)
from app.services.entity_service import get_current_approved_entities
from app.services.failure_reason_service import normalize_failure_reason
from app.services.image_consistency_service import evaluate_image_consistency
from app.services.image_provider_profile_service import ImageProviderSelection, resolve_image_provider_selection
from app.services.pre_image_service import assert_project_pre_image_requirements
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
from app.services.task_service import (
    TaskCompletionConflictError,
    begin_task_completion,
    mark_task_failed,
    mark_task_succeeded,
    update_task_progress,
)
from app.services.workflow_state_service import (
    mark_downstream_stages_pending,
    mark_stage_failed,
    mark_stage_ready,
    mark_stage_running,
)


@dataclass(frozen=True)
class ImageCandidateTarget:
    project_id: str
    entity_type: str
    entity_id: str
    asset_role: str
    variant_key: str | None
    label: str
    prompt: str
    negative_prompt: str | None
    asset_resolution: AssetResolution | None


def generate_reference_images(db: Session, project_id: str) -> tuple[list[Asset], GenerationTask]:
    project = get_project_or_404(db, project_id)
    image_provider = provider_registry.get(ProviderType.IMAGE, settings.image_provider)
    characters, scenes, _props = get_current_approved_entities(db, project_id)
    characters = [item for item in characters if asset_spec_reference_required(item.asset_spec)]
    scenes = [item for item in scenes if asset_spec_reference_required(item.asset_spec)]
    if not (characters or scenes):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No approved characters or scenes require reference images",
        )

    task = create_generation_task(
        db,
        project_id=project_id,
        task_type="reference_image_generation",
        input_payload={
            "character_ids": [character.id for character in characters],
            "scene_ids": [scene.id for scene in scenes],
            "prop_ids": [],
            "default_image_entity_types": ["character", "scene"],
        },
        provider=image_provider.name,
        model=image_provider.model,
    )
    mark_stage_running(db, project_id, "images", task_id=task.id)
    assets: list[Asset] = []
    total = len(characters) + len(scenes)
    reference_resolution = resolve_generation_assets(db, project_id, stage="reference_image")
    try:
        completed = 0
        for character in characters:
            update_task_progress(db, task, f"正在生成角色多视角设定图：{character.name}", batch_progress(completed, total))
            assets.append(
                _generate_image_asset(
                    db,
                    project_id=project_id,
                    task_id=task.id,
                    entity_type="character",
                    entity_id=character.id,
                    asset_role="character_main_ref",
                    prompt=character_reference_prompt(character, _visual_style(project), "character_main_ref"),
                    negative_prompt=reference_negative_prompt(character, "character_main_ref"),
                    asset_resolution=reference_resolution,
                )
            )
            completed += 1
        for scene in scenes:
            update_task_progress(db, task, f"正在生成场景参考图：{scene.name}", batch_progress(completed, total))
            assets.append(
                _generate_image_asset(
                    db,
                    project_id=project_id,
                    task_id=task.id,
                    entity_type="scene",
                    entity_id=scene.id,
                    asset_role="scene_ref",
                    prompt=scene_reference_prompt(scene, _visual_style(project)),
                    negative_prompt=reference_negative_prompt(scene, "scene_ref"),
                    asset_resolution=reference_resolution,
                )
            )
            completed += 1
    except Exception as exc:
        code, message, raw_response = extract_task_error(exc)
        failure_code = code or "reference_image_generation_failed"
        mark_task_failed(db, task, code=failure_code, message=message, raw_response=raw_response)
        mark_stage_failed(
            db,
            project_id,
            "images",
            summary=message,
            task_id=task.id,
            error_code=failure_code,
            failure_reason=normalize_failure_reason(code=failure_code, message=message, raw_response=raw_response, stage="images"),
        )
        db.commit()
        raise

    mark_stage_ready(
        db,
        project_id,
        "images",
        task_id=task.id,
        summary=f"{len(assets)} 张参考图候选已生成，等待采用",
        metadata={"asset_count": len(assets), "kind": "reference_images"},
    )
    mark_downstream_stages_pending(db, project_id, "images", summary="图片资产已更新，需要重新生成视频和后续内容")
    finish_generation_task(db, task, assets)
    return assets, task


def generate_reference_image_candidates(
    db: Session,
    project_id: str,
    *,
    image_provider_profile_id: str | None = None,
) -> tuple[list[AssetCandidate], GenerationTask]:
    project = get_project_or_404(db, project_id)
    image_provider_selection = resolve_image_provider_selection(db, image_provider_profile_id)
    image_provider = image_provider_selection.provider
    characters, scenes, _props = get_current_approved_entities(db, project_id)
    characters = [item for item in characters if asset_spec_reference_required(item.asset_spec)]
    scenes = [item for item in scenes if asset_spec_reference_required(item.asset_spec)]
    if not (characters or scenes):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No approved characters or scenes require reference images",
        )

    task = create_generation_task(
        db,
        project_id=project_id,
        task_type="reference_image_candidate_generation",
        input_payload={
            "character_ids": [character.id for character in characters],
            "scene_ids": [scene.id for scene in scenes],
            "prop_ids": [],
            "default_image_entity_types": ["character", "scene"],
            "candidate_policy": "pending_review_before_asset",
            "image_provider_profile_id": image_provider_selection.profile_id,
        },
        provider=image_provider.name,
        model=image_provider.model,
    )
    mark_stage_running(db, project_id, "images", task_id=task.id)
    candidates: list[AssetCandidate] = []
    total = len(characters) + len(scenes)
    reference_resolution = resolve_generation_assets(db, project_id, stage="reference_image")
    try:
        completed = 0
        for character in characters:
            update_task_progress(db, task, f"正在生成角色多视角设定图候选：{character.name}", batch_progress(completed, total))
            candidates.append(
                _generate_image_candidate(
                    db,
                    project_id=project_id,
                    task_id=task.id,
                    entity_type="character",
                    entity_id=character.id,
                    asset_role="character_main_ref",
                    prompt=character_reference_prompt(character, _visual_style(project), "character_main_ref"),
                    negative_prompt=reference_negative_prompt(character, "character_main_ref"),
                    asset_resolution=reference_resolution,
                    image_provider_selection=image_provider_selection,
                )
            )
            completed += 1
        for scene in scenes:
            update_task_progress(db, task, f"正在生成场景参考图候选：{scene.name}", batch_progress(completed, total))
            candidates.append(
                _generate_image_candidate(
                    db,
                    project_id=project_id,
                    task_id=task.id,
                    entity_type="scene",
                    entity_id=scene.id,
                    asset_role="scene_ref",
                    prompt=scene_reference_prompt(scene, _visual_style(project)),
                    negative_prompt=reference_negative_prompt(scene, "scene_ref"),
                    asset_resolution=reference_resolution,
                    image_provider_selection=image_provider_selection,
                )
            )
            completed += 1
    except Exception as exc:
        code, message, raw_response = extract_task_error(exc)
        failure_code = code or "reference_image_candidate_generation_failed"
        mark_task_failed(db, task, code=failure_code, message=message, raw_response=raw_response)
        mark_stage_failed(
            db,
            project_id,
            "images",
            summary=message,
            task_id=task.id,
            error_code=failure_code,
            failure_reason=normalize_failure_reason(code=failure_code, message=message, raw_response=raw_response, stage="images"),
        )
        db.commit()
        raise

    mark_stage_ready(
        db,
        project_id,
        "images",
        task_id=task.id,
        summary=f"{len(candidates)} 张参考图候选已生成，等待确认入库",
        metadata={"candidate_count": len(candidates), "kind": "reference_image_candidates"},
    )
    finish_candidate_generation_task(db, task, candidates)
    return candidates, task


def generate_single_reference_image_candidate(
    db: Session,
    project_id: str,
    *,
    entity_type: str,
    entity_id: str,
    asset_role: str,
    variant_key: str = "base",
    image_provider_profile_id: str | None = None,
) -> tuple[AssetCandidate, GenerationTask]:
    project = get_project_or_404(db, project_id)
    characters, scenes, props = get_current_approved_entities(db, project_id)
    allowed_roles = {
        "character": {"character_main_ref"},
        "scene": {"scene_ref"},
        "prop": {"prop_ref"},
    }
    if entity_type not in allowed_roles or asset_role not in allowed_roles[entity_type]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported reference image target",
        )

    entity_groups: dict[str, list[Character] | list[Scene] | list[Prop]] = {
        "character": characters,
        "scene": scenes,
        "prop": props,
    }
    entity = next((item for item in entity_groups[entity_type] if item.id == entity_id), None)
    if entity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approved reference image entity not found",
        )
    _assert_resource_task_available(
        db,
        project_id,
        resource_type=entity_type,
        resource_id=entity_id,
    )

    if isinstance(entity, Character) and (variant_key.strip() or "base") != "base":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Character state variants are prompt-only; generate the base multi-view reference instead",
        )

    normalized_variant_key, variant_description = _resolve_state_variant(entity, variant_key)
    if isinstance(entity, Character):
        prompt = character_reference_prompt(entity, _visual_style(project), asset_role)
    elif isinstance(entity, Scene):
        prompt = scene_reference_prompt(entity, _visual_style(project))
    else:
        prompt = prop_reference_prompt(entity, _visual_style(project))
    if variant_description:
        prompt = f"{prompt}；剧情状态变体：{variant_description}"
    negative_prompt = reference_negative_prompt(entity, asset_role)

    image_provider_selection = resolve_image_provider_selection(db, image_provider_profile_id)
    image_provider = image_provider_selection.provider
    asset_resolution = resolve_generation_assets(db, project_id, stage="reference_image")
    task = create_generation_task(
        db,
        project_id=project_id,
        task_type="single_reference_image_candidate_generation",
        input_payload={
            "entity_type": entity_type,
            "entity_id": entity_id,
            "asset_role": asset_role,
            "variant_key": normalized_variant_key,
            "candidate_policy": "pending_review_before_asset",
            "image_provider_profile_id": image_provider_selection.profile_id,
        },
        provider=image_provider.name,
        model=image_provider.model,
    )
    mark_stage_running(db, project_id, "images", task_id=task.id)
    update_task_progress(db, task, f"正在生成单张资产图候选：{entity.name}", 20)
    try:
        candidate = _generate_image_candidate(
            db,
            project_id=project_id,
            task_id=task.id,
            entity_type=entity_type,
            entity_id=entity_id,
            asset_role=asset_role,
            variant_key=normalized_variant_key,
            prompt=prompt,
            negative_prompt=negative_prompt,
            asset_resolution=asset_resolution,
            image_provider_selection=image_provider_selection,
        )
    except Exception as exc:
        code, message, raw_response = extract_task_error(exc)
        failure_code = code or "single_reference_image_candidate_generation_failed"
        mark_task_failed(db, task, code=failure_code, message=message, raw_response=raw_response)
        mark_stage_failed(
            db,
            project_id,
            "images",
            summary=message,
            task_id=task.id,
            error_code=failure_code,
            failure_reason=normalize_failure_reason(
                code=failure_code,
                message=message,
                raw_response=raw_response,
                stage="images",
            ),
        )
        db.commit()
        raise

    mark_stage_ready(
        db,
        project_id,
        "images",
        task_id=task.id,
        summary=f"{entity.name} 的单张资产图候选已生成，等待确认入库",
        metadata={
            "candidate_count": 1,
            "kind": "single_reference_image_candidate",
            "entity_type": entity_type,
            "entity_id": entity_id,
            "asset_role": asset_role,
        },
    )
    finish_candidate_generation_task(db, task, [candidate])
    return candidate, task


def generate_shot_images(db: Session, project_id: str) -> tuple[list[Asset], GenerationTask]:
    project = get_project_or_404(db, project_id)
    assert_project_pre_image_requirements(db, project_id)
    image_provider = provider_registry.get(ProviderType.IMAGE, settings.image_provider)
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
            detail="Video segments are required before storyboard image generation",
        )

    visual_style = _visual_style(project)
    image_prompts = {
        shot.id: _shot_image_prompt_data(shot, "shot_storyboard", style=visual_style)
        for shot in shots
    }
    asset_resolutions = {
        shot.id: resolve_generation_assets(
            db,
            project_id,
            stage="shot_image",
            shot=shot,
            visible_character_ids=image_prompts[shot.id]["visible_character_ids"],
            visible_prop_ids=image_prompts[shot.id]["visible_prop_ids"],
        )
        for shot in shots
    }
    task = create_generation_task(
        db,
        project_id=project_id,
        task_type="shot_image_generation",
        input_payload={
            "shot_ids": [shot.id for shot in shots],
            "asset_resolutions": [resolution.to_task_payload() for resolution in asset_resolutions.values()],
        },
        provider=image_provider.name,
        model=image_provider.model,
    )
    mark_stage_running(db, project_id, "images", task_id=task.id)
    assets = []
    try:
        for index, shot in enumerate(shots, start=1):
            update_task_progress(db, task, f"正在生成分镜图：镜头 {shot.shot_no}", batch_progress(index - 1, len(shots)))
            assets.append(
                _generate_image_asset(
                    db,
                    project_id=project_id,
                    task_id=task.id,
                    entity_type="shot",
                    entity_id=shot.id,
                    asset_role="shot_storyboard",
                    prompt=image_prompts[shot.id]["positive_prompt"],
                    negative_prompt=image_prompts[shot.id]["negative_prompt"],
                    asset_resolution=asset_resolutions[shot.id],
                )
            )
    except Exception as exc:
        code, message, raw_response = extract_task_error(exc)
        failure_code = code or "shot_image_generation_failed"
        mark_task_failed(db, task, code=failure_code, message=message, raw_response=raw_response)
        mark_stage_failed(
            db,
            project_id,
            "images",
            summary=message,
            task_id=task.id,
            error_code=failure_code,
            failure_reason=normalize_failure_reason(code=failure_code, message=message, raw_response=raw_response, stage="images"),
        )
        db.commit()
        raise
    mark_stage_ready(
        db,
        project_id,
        "images",
        task_id=task.id,
        summary=f"{len(assets)} 张分镜图候选已生成，等待采用",
        metadata={"asset_count": len(assets), "kind": "shot_images"},
    )
    mark_downstream_stages_pending(db, project_id, "images", summary="图片资产已更新，需要重新生成视频和后续内容")
    finish_generation_task(db, task, assets)
    return assets, task


def generate_shot_image_candidates(
    db: Session,
    project_id: str,
    *,
    image_provider_profile_id: str | None = None,
) -> tuple[list[AssetCandidate], GenerationTask]:
    project = get_project_or_404(db, project_id)
    assert_project_pre_image_requirements(db, project_id)
    image_provider_selection = resolve_image_provider_selection(db, image_provider_profile_id)
    image_provider = image_provider_selection.provider
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
            detail="Video segments are required before storyboard image generation",
        )

    visual_style = _visual_style(project)
    image_prompts = {
        shot.id: _shot_image_prompt_data(shot, "shot_storyboard", style=visual_style)
        for shot in shots
    }
    asset_resolutions = {
        shot.id: resolve_generation_assets(
            db,
            project_id,
            stage="shot_image",
            shot=shot,
            visible_character_ids=image_prompts[shot.id]["visible_character_ids"],
            visible_prop_ids=image_prompts[shot.id]["visible_prop_ids"],
        )
        for shot in shots
    }
    task = create_generation_task(
        db,
        project_id=project_id,
        task_type="shot_image_candidate_generation",
        input_payload={
            "shot_ids": [shot.id for shot in shots],
            "asset_resolutions": [resolution.to_task_payload() for resolution in asset_resolutions.values()],
            "candidate_policy": "pending_review_before_asset",
            "candidate_count_per_shot": _image_candidate_count(),
            "image_provider_profile_id": image_provider_selection.profile_id,
        },
        provider=image_provider.name,
        model=image_provider.model,
    )
    mark_stage_running(db, project_id, "images", task_id=task.id)
    candidates: list[AssetCandidate] = []
    try:
        candidate_count = _image_candidate_count()
        total_candidates = len(shots) * candidate_count
        generated = 0
        for shot in shots:
            for candidate_index in range(candidate_count):
                update_task_progress(
                    db,
                    task,
                    f"正在生成分镜图候选：镜头 {shot.shot_no}（{candidate_index + 1}/{candidate_count}）",
                    batch_progress(generated, total_candidates),
                )
                candidates.append(
                    _generate_image_candidate(
                        db,
                        project_id=project_id,
                        task_id=task.id,
                        entity_type="shot",
                        entity_id=shot.id,
                        asset_role="shot_storyboard",
                        prompt=image_prompts[shot.id]["positive_prompt"],
                        negative_prompt=image_prompts[shot.id]["negative_prompt"],
                        asset_resolution=asset_resolutions[shot.id],
                        image_provider_selection=image_provider_selection,
                    )
                )
                generated += 1
    except Exception as exc:
        code, message, raw_response = extract_task_error(exc)
        failure_code = code or "shot_image_candidate_generation_failed"
        mark_task_failed(db, task, code=failure_code, message=message, raw_response=raw_response)
        mark_stage_failed(
            db,
            project_id,
            "images",
            summary=message,
            task_id=task.id,
            error_code=failure_code,
            failure_reason=normalize_failure_reason(code=failure_code, message=message, raw_response=raw_response, stage="images"),
        )
        db.commit()
        raise
    mark_stage_ready(
        db,
        project_id,
        "images",
        task_id=task.id,
        summary=f"{len(candidates)} 张分镜图候选已生成，等待确认入库",
        metadata={"candidate_count": len(candidates), "candidate_count_per_shot": _image_candidate_count(), "kind": "shot_image_candidates"},
    )
    finish_candidate_generation_task(db, task, candidates)
    return candidates, task


def generate_single_shot_image_candidate(
    db: Session,
    shot_id: str,
    *,
    image_provider_profile_id: str | None = None,
) -> tuple[AssetCandidate, GenerationTask]:
    shot = db.get(Shot, shot_id)
    if shot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shot not found")
    if not shot.is_current:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Current shot is required before storyboard image generation",
        )
    _assert_resource_task_available(
        db,
        shot.project_id,
        resource_type="shot",
        resource_id=shot.id,
    )

    project = get_project_or_404(db, shot.project_id)
    assert_project_pre_image_requirements(db, project.id, shot_ids={shot.id})
    image_provider_selection = resolve_image_provider_selection(db, image_provider_profile_id)
    image_provider = image_provider_selection.provider
    image_prompt = _shot_image_prompt_data(
        shot,
        "shot_storyboard",
        style=_visual_style(project),
    )
    asset_resolution = resolve_generation_assets(
        db,
        project.id,
        stage="shot_image",
        shot=shot,
        visible_character_ids=image_prompt["visible_character_ids"],
        visible_prop_ids=image_prompt["visible_prop_ids"],
    )
    task = create_generation_task(
        db,
        project_id=project.id,
        task_type="single_shot_image_candidate_generation",
        input_payload={
            "shot_id": shot.id,
            "asset_resolution": asset_resolution.to_task_payload(),
            "candidate_policy": "pending_review_before_asset",
            "image_provider_profile_id": image_provider_selection.profile_id,
        },
        provider=image_provider.name,
        model=image_provider.model,
    )
    mark_stage_running(db, project.id, "images", task_id=task.id)
    update_task_progress(db, task, f"正在生成分镜图候选：镜头 {shot.shot_no}", 20)
    try:
        candidate = _generate_image_candidate(
            db,
            project_id=project.id,
            task_id=task.id,
            entity_type="shot",
            entity_id=shot.id,
            asset_role="shot_storyboard",
            prompt=image_prompt["positive_prompt"],
            negative_prompt=image_prompt["negative_prompt"],
            asset_resolution=asset_resolution,
            image_provider_selection=image_provider_selection,
        )
    except Exception as exc:
        code, message, raw_response = extract_task_error(exc)
        mark_task_failed(
            db,
            task,
            code=code or "single_shot_image_candidate_generation_failed",
            message=message,
            raw_response=raw_response,
        )
        mark_stage_failed(
            db,
            project.id,
            "images",
            summary=message,
            task_id=task.id,
            error_code=code or "single_shot_image_candidate_generation_failed",
            failure_reason=normalize_failure_reason(
                code=code or "single_shot_image_candidate_generation_failed",
                message=message,
                raw_response=raw_response,
                stage="images",
            ),
        )
        db.commit()
        raise

    mark_stage_ready(
        db,
        project.id,
        "images",
        task_id=task.id,
        summary=f"镜头 {shot.shot_no} 的分镜图候选已生成，等待确认入库",
        metadata={
            "candidate_count": 1,
            "kind": "single_shot_image_candidate",
            "shot_id": shot.id,
        },
    )
    finish_candidate_generation_task(db, task, [candidate])
    return candidate, task


def generate_shot_image_grid(
    db: Session,
    project_id: str,
    *,
    rows: int = 2,
    cols: int = 2,
) -> tuple[list[Asset], GenerationTask]:
    project = get_project_or_404(db, project_id)
    image_provider = provider_registry.get(ProviderType.IMAGE, settings.image_provider)
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
            detail="Video segments are required before storyboard image generation",
        )

    cell_count = max(1, min(rows * cols, 12))
    selected_shots = shots[:cell_count]
    task = create_generation_task(
        db,
        project_id=project_id,
        task_type="shot_image_grid_generation",
        input_payload={
            "rows": rows,
            "cols": cols,
            "shot_ids": [shot.id for shot in selected_shots],
        },
        provider=image_provider.name,
        model=image_provider.model,
    )
    mark_stage_running(db, project_id, "images", task_id=task.id)

    update_task_progress(db, task, "正在生成宫格图提示词", 20)
    prompt = _build_grid_image_prompt(
        selected_shots,
        rows,
        cols,
        style=_visual_style(project),
    )
    generation_params, generation_seed, seed_strategy = _image_generation_params(
        task_id=task.id,
        target_kind="asset",
        entity_type="grid",
        entity_id=task.id,
        asset_role="shot_storyboard_grid",
        version=1,
        extra_params=None,
    )
    request_metadata = {
        "entity_type": "grid",
        "rows": rows,
        "cols": cols,
        "generation_seed": generation_seed,
        "seed_strategy": seed_strategy,
    }
    try:
        provider_response = image_provider.submit(
            ProviderRequest(
                project_id=project_id,
                task_id=f"{task.id}:grid:{rows}x{cols}",
                model=image_provider.model,
                prompt=prompt,
                negative_prompt="low quality, watermark, text, missing panels, merged panels, distorted faces",
                params=generation_params,
                metadata=request_metadata,
            )
        )
        grid_asset = _persist_provider_image_asset(
            db,
            project_id=project_id,
            entity_type="grid",
            entity_id=task.id,
            asset_role="shot_storyboard_grid",
            source_task_id=task.id,
            provider_name=image_provider.name,
            model=image_provider.model,
            prompt=prompt,
            negative_prompt="low quality, watermark, text, missing panels, merged panels, distorted faces",
            response=provider_response,
            request_metadata=request_metadata,
        )
        grid_asset.source_script_id = selected_shots[0].script_id if selected_shots else None
        grid_asset.source_shot_batch_id = selected_shots[0].shot_batch_id if selected_shots else None
        db.add(grid_asset)
        update_task_progress(db, task, "正在切分宫格图", 75)
        cell_assets = _persist_grid_cell_assets(
            db,
            project_id=project_id,
            grid_asset=grid_asset,
            shots=selected_shots,
            rows=rows,
            cols=cols,
            style=_visual_style(project),
        )
    except Exception as exc:
        code, message, raw_response = extract_task_error(exc)
        failure_code = code or "shot_image_grid_generation_failed"
        mark_task_failed(db, task, code=failure_code, message=message, raw_response=raw_response)
        mark_stage_failed(
            db,
            project_id,
            "images",
            summary=message,
            task_id=task.id,
            error_code=failure_code,
            failure_reason=normalize_failure_reason(code=failure_code, message=message, raw_response=raw_response, stage="images"),
        )
        db.commit()
        raise

    assets = [grid_asset, *cell_assets]
    mark_stage_ready(
        db,
        project_id,
        "images",
        task_id=task.id,
        summary=f"{len(assets)} 张宫格分镜资产候选已生成，等待采用",
        metadata={"asset_count": len(assets), "kind": "shot_image_grid"},
    )
    mark_downstream_stages_pending(db, project_id, "images", summary="图片资产已更新，需要重新生成视频和后续内容")
    finish_generation_task(db, task, assets)
    return assets, task


def regenerate_image_asset(db: Session, asset_id: str) -> tuple[Asset, GenerationTask]:
    source_asset = db.get(Asset, asset_id)
    if source_asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    if source_asset.asset_type != "image":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only image assets can be regenerated")
    if not source_asset.entity_type or not source_asset.entity_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Asset target is missing")

    image_prompt = _stored_image_prompt_for_target(
        db,
        project_id=source_asset.project_id,
        entity_type=source_asset.entity_type,
        entity_id=source_asset.entity_id,
        asset_role=source_asset.asset_role,
    )
    prompt = image_prompt["positive_prompt"]
    negative_prompt = image_prompt["negative_prompt"]
    variant_key = source_asset.variant_key or (
        "base" if source_asset.entity_type in {"character", "scene", "prop"} else None
    )
    prompt = _prompt_with_state_variant(
        db,
        entity_type=source_asset.entity_type,
        entity_id=source_asset.entity_id,
        variant_key=variant_key,
        prompt=prompt,
    )
    image_provider = provider_registry.get(ProviderType.IMAGE, settings.image_provider)
    asset_resolution = _resolve_image_asset_generation_context(db, source_asset, prompt_data=image_prompt)
    task = create_generation_task(
        db,
        project_id=source_asset.project_id,
        task_type="image_asset_regeneration",
        input_payload={
            "source_asset_id": source_asset.id,
            "entity_type": source_asset.entity_type,
            "entity_id": source_asset.entity_id,
            "asset_role": source_asset.asset_role,
            "variant_key": variant_key,
            "version": source_asset.version,
            "asset_resolution": asset_resolution.to_task_payload() if asset_resolution else None,
        },
        provider=image_provider.name,
        model=image_provider.model,
    )
    update_task_progress(db, task, "正在重生成图片资产", 20)
    try:
        asset = _generate_image_asset(
            db,
            project_id=source_asset.project_id,
            task_id=task.id,
            entity_type=source_asset.entity_type,
            entity_id=source_asset.entity_id,
            asset_role=source_asset.asset_role,
            variant_key=variant_key,
            prompt=prompt,
            negative_prompt=negative_prompt,
            asset_resolution=asset_resolution,
        )
    except Exception as exc:
        code, message, raw_response = extract_task_error(exc)
        mark_task_failed(db, task, code=code or "image_asset_regeneration_failed", message=message, raw_response=raw_response)
        db.commit()
        raise

    finish_generation_task(db, task, [asset])
    return asset, task


def regenerate_image_asset_candidate(
    db: Session,
    asset_id: str,
    *,
    image_provider_profile_id: str | None = None,
) -> tuple[AssetCandidate, GenerationTask]:
    source_asset = db.get(Asset, asset_id)
    if source_asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
    if source_asset.asset_type != "image":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only image assets can be regenerated")
    if not source_asset.entity_type or not source_asset.entity_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Asset target is missing")

    image_prompt = _stored_image_prompt_for_target(
        db,
        project_id=source_asset.project_id,
        entity_type=source_asset.entity_type,
        entity_id=source_asset.entity_id,
        asset_role=source_asset.asset_role,
    )
    prompt = image_prompt["positive_prompt"]
    negative_prompt = image_prompt["negative_prompt"]
    variant_key = source_asset.variant_key or (
        "base" if source_asset.entity_type in {"character", "scene", "prop"} else None
    )
    prompt = _prompt_with_state_variant(
        db,
        entity_type=source_asset.entity_type,
        entity_id=source_asset.entity_id,
        variant_key=variant_key,
        prompt=prompt,
    )
    image_provider_selection = resolve_image_provider_selection(db, image_provider_profile_id)
    image_provider = image_provider_selection.provider
    asset_resolution = _resolve_image_asset_generation_context(db, source_asset, prompt_data=image_prompt)
    task = create_generation_task(
        db,
        project_id=source_asset.project_id,
        task_type="image_asset_regeneration_candidate",
        input_payload={
            "source_asset_id": source_asset.id,
            "entity_type": source_asset.entity_type,
            "entity_id": source_asset.entity_id,
            "asset_role": source_asset.asset_role,
            "variant_key": variant_key,
            "version": source_asset.version,
            "candidate_policy": "pending_review_before_asset",
            "asset_resolution": asset_resolution.to_task_payload() if asset_resolution else None,
            "image_provider_profile_id": image_provider_selection.profile_id,
        },
        provider=image_provider.name,
        model=image_provider.model,
    )
    update_task_progress(db, task, "正在重生成图片候选", 20)
    try:
        candidate = _generate_image_candidate(
            db,
            project_id=source_asset.project_id,
            task_id=task.id,
            entity_type=source_asset.entity_type,
            entity_id=source_asset.entity_id,
            asset_role=source_asset.asset_role,
            variant_key=variant_key,
            prompt=prompt,
            negative_prompt=negative_prompt,
            asset_resolution=asset_resolution,
            image_provider_selection=image_provider_selection,
        )
    except Exception as exc:
        code, message, raw_response = extract_task_error(exc)
        mark_task_failed(db, task, code=code or "image_asset_regeneration_candidate_failed", message=message, raw_response=raw_response)
        db.commit()
        raise

    finish_candidate_generation_task(db, task, [candidate])
    return candidate, task


def regenerate_image_candidate_from_candidate(
    db: Session,
    source_candidate: AssetCandidate,
    *,
    image_provider_profile_id: str | None = None,
) -> tuple[AssetCandidate, GenerationTask]:
    if source_candidate.asset_type != "image":
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Only image candidates can be regenerated")
    if not source_candidate.entity_type or not source_candidate.entity_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Candidate target is missing")

    resolved_profile_id = image_provider_profile_id or _candidate_image_provider_profile_id(source_candidate)
    image_provider_selection = resolve_image_provider_selection(db, resolved_profile_id)
    image_provider = image_provider_selection.provider
    image_prompt = _stored_image_prompt_for_target(
        db,
        project_id=source_candidate.project_id,
        entity_type=source_candidate.entity_type,
        entity_id=source_candidate.entity_id,
        asset_role=source_candidate.asset_role,
    )
    prompt = image_prompt["positive_prompt"]
    negative_prompt = image_prompt["negative_prompt"]
    variant_key = source_candidate.variant_key or (
        "base" if source_candidate.entity_type in {"character", "scene", "prop"} else None
    )
    prompt = _prompt_with_state_variant(
        db,
        entity_type=source_candidate.entity_type,
        entity_id=source_candidate.entity_id,
        variant_key=variant_key,
        prompt=prompt,
    )
    asset_resolution = _resolve_image_generation_context_for_target(
        db,
        project_id=source_candidate.project_id,
        entity_type=source_candidate.entity_type,
        entity_id=source_candidate.entity_id,
        asset_role=source_candidate.asset_role,
        prompt_data=image_prompt,
    )
    task = create_generation_task(
        db,
        project_id=source_candidate.project_id,
        task_type="image_candidate_regeneration",
        input_payload={
            "source_candidate_id": source_candidate.id,
            "entity_type": source_candidate.entity_type,
            "entity_id": source_candidate.entity_id,
            "asset_role": source_candidate.asset_role,
            "variant_key": variant_key,
            "version": source_candidate.version,
            "candidate_policy": "pending_review_before_asset",
            "asset_resolution": asset_resolution.to_task_payload() if asset_resolution else None,
            "image_provider_profile_id": image_provider_selection.profile_id,
        },
        provider=image_provider.name,
        model=image_provider.model,
    )
    update_task_progress(db, task, "正在重新生成图片候选", 20)
    try:
        candidate = _generate_image_candidate(
            db,
            project_id=source_candidate.project_id,
            task_id=task.id,
            entity_type=source_candidate.entity_type,
            entity_id=source_candidate.entity_id,
            asset_role=source_candidate.asset_role,
            variant_key=variant_key,
            prompt=prompt,
            negative_prompt=negative_prompt,
            asset_resolution=asset_resolution,
            image_provider_selection=image_provider_selection,
        )
    except Exception as exc:
        code, message, raw_response = extract_task_error(exc)
        mark_task_failed(
            db,
            task,
            code=code or "image_candidate_regeneration_failed",
            message=message,
            raw_response=raw_response,
        )
        db.commit()
        raise

    finish_candidate_generation_task(db, task, [candidate])
    return candidate, task


def prepare_reference_image_candidate_targets(
    db: Session,
    project_id: str,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    asset_role: str | None = None,
    variant_key: str = "base",
) -> list[ImageCandidateTarget]:
    """Compile reference-image targets without submitting generation requests."""
    project = get_project_or_404(db, project_id)
    characters, scenes, props = get_current_approved_entities(db, project_id)
    resolution = resolve_generation_assets(db, project_id, stage="reference_image")
    if entity_type is None:
        targets: list[ImageCandidateTarget] = []
        for character in characters:
            if not asset_spec_reference_required(character.asset_spec):
                continue
            targets.append(
                ImageCandidateTarget(
                    project_id=project_id,
                    entity_type="character",
                    entity_id=character.id,
                    asset_role="character_main_ref",
                    variant_key="base",
                    label=character.name,
                    prompt=character_reference_prompt(
                        character,
                        _visual_style(project),
                        "character_main_ref",
                    ),
                    negative_prompt=reference_negative_prompt(
                        character,
                        "character_main_ref",
                    ),
                    asset_resolution=resolution,
                )
            )
        for scene in scenes:
            if not asset_spec_reference_required(scene.asset_spec):
                continue
            targets.append(
                ImageCandidateTarget(
                    project_id=project_id,
                    entity_type="scene",
                    entity_id=scene.id,
                    asset_role="scene_ref",
                    variant_key="base",
                    label=scene.name,
                    prompt=scene_reference_prompt(scene, _visual_style(project)),
                    negative_prompt=reference_negative_prompt(scene, "scene_ref"),
                    asset_resolution=resolution,
                )
            )
        if not targets:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No approved characters or scenes require reference images",
            )
        return targets

    allowed_roles = {
        "character": {"character_main_ref"},
        "scene": {"scene_ref"},
        "prop": {"prop_ref"},
    }
    if (
        entity_type not in allowed_roles
        or asset_role not in allowed_roles[entity_type]
        or not entity_id
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported reference image target",
        )
    entity_groups: dict[str, list[Character] | list[Scene] | list[Prop]] = {
        "character": characters,
        "scene": scenes,
        "prop": props,
    }
    entity = next(
        (item for item in entity_groups[entity_type] if item.id == entity_id),
        None,
    )
    if entity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approved reference image entity not found",
        )
    if isinstance(entity, Character) and (variant_key.strip() or "base") != "base":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Character state variants are prompt-only; generate the base multi-view reference instead",
        )
    normalized_variant, variant_description = _resolve_state_variant(entity, variant_key)
    if isinstance(entity, Character):
        prompt = character_reference_prompt(entity, _visual_style(project), asset_role)
    elif isinstance(entity, Scene):
        prompt = scene_reference_prompt(entity, _visual_style(project))
    else:
        prompt = prop_reference_prompt(entity, _visual_style(project))
    if variant_description:
        prompt = f"{prompt}；剧情状态变体：{variant_description}"
    return [
        ImageCandidateTarget(
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity.id,
            asset_role=asset_role,
            variant_key=normalized_variant,
            label=entity.name,
            prompt=prompt,
            negative_prompt=reference_negative_prompt(entity, asset_role),
            asset_resolution=resolution,
        )
    ]


def prepare_shot_image_candidate_targets(
    db: Session,
    project_id: str,
    *,
    shot_id: str | None = None,
) -> list[ImageCandidateTarget]:
    """Compile current storyboard-image targets without calling the Provider."""
    project = get_project_or_404(db, project_id)
    requested_ids = {shot_id} if shot_id else None
    assert_project_pre_image_requirements(db, project_id, shot_ids=requested_ids)
    statement = (
        select(Shot)
        .where(Shot.project_id == project_id)
        .where(Shot.is_current.is_(True))
        .order_by(Shot.shot_no)
    )
    if shot_id:
        statement = statement.where(Shot.id == shot_id)
    shots = list(db.scalars(statement).all())
    if not shots:
        detail = "Shot not found" if shot_id else "Video segments are required before storyboard image generation"
        raise HTTPException(
            status_code=(status.HTTP_404_NOT_FOUND if shot_id else status.HTTP_409_CONFLICT),
            detail=detail,
        )
    targets: list[ImageCandidateTarget] = []
    for shot in shots:
        prompt_data = _shot_image_prompt_data(
            shot,
            "shot_storyboard",
            style=_visual_style(project),
        )
        resolution = resolve_generation_assets(
            db,
            project_id,
            stage="shot_image",
            shot=shot,
            visible_character_ids=prompt_data["visible_character_ids"],
            visible_prop_ids=prompt_data["visible_prop_ids"],
        )
        targets.append(
            ImageCandidateTarget(
                project_id=project_id,
                entity_type="shot",
                entity_id=shot.id,
                asset_role="shot_storyboard",
                variant_key=None,
                label=f"镜头 {shot.shot_no}",
                prompt=prompt_data["positive_prompt"],
                negative_prompt=prompt_data["negative_prompt"],
                asset_resolution=resolution,
            )
        )
    return targets


def prepare_image_regeneration_candidate_target(
    db: Session,
    source: Asset | AssetCandidate,
) -> ImageCandidateTarget:
    """Freeze the current prompt and references for an explicit regeneration."""
    if source.asset_type != "image":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only image assets can be regenerated",
        )
    if not source.entity_type or not source.entity_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Image target is missing",
        )
    prompt_data = _stored_image_prompt_for_target(
        db,
        project_id=source.project_id,
        entity_type=source.entity_type,
        entity_id=source.entity_id,
        asset_role=source.asset_role,
    )
    variant_key = source.variant_key or (
        "base" if source.entity_type in {"character", "scene", "prop"} else None
    )
    prompt = _prompt_with_state_variant(
        db,
        entity_type=source.entity_type,
        entity_id=source.entity_id,
        variant_key=variant_key,
        prompt=prompt_data["positive_prompt"],
    )
    resolution = _resolve_image_generation_context_for_target(
        db,
        project_id=source.project_id,
        entity_type=source.entity_type,
        entity_id=source.entity_id,
        asset_role=source.asset_role,
        prompt_data=prompt_data,
    )
    return ImageCandidateTarget(
        project_id=source.project_id,
        entity_type=source.entity_type,
        entity_id=source.entity_id,
        asset_role=source.asset_role or infer_asset_role(
            asset_type="image",
            entity_type=source.entity_type,
            entity_id=source.entity_id,
        ),
        variant_key=variant_key,
        label=f"{source.entity_type}:{source.entity_id}",
        prompt=prompt,
        negative_prompt=prompt_data["negative_prompt"],
        asset_resolution=resolution,
    )


def image_candidate_provider_profile_id(candidate: AssetCandidate) -> str | None:
    return _candidate_image_provider_profile_id(candidate)


def compile_image_candidate_task_input(
    db: Session,
    *,
    task_id: str,
    project_id: str,
    entity_type: str,
    entity_id: str,
    asset_role: str | None,
    variant_key: str | None,
    prompt: str,
    negative_prompt: str | None,
    asset_resolution: AssetResolution | None,
    image_provider_selection: ImageProviderSelection,
    operation: str,
    source_asset_id: str | None = None,
    source_asset_version: int | None = None,
    source_candidate_id: str | None = None,
    source_candidate_version: int | None = None,
    extra_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Freeze a single image candidate request without calling the Provider."""
    provider = image_provider_selection.provider
    resolved_asset_role = asset_role or infer_asset_role(
        asset_type="image",
        entity_type=entity_type,
        entity_id=entity_id,
    )
    resolved_variant_key = variant_key or (
        "base" if entity_type in {"character", "scene", "prop"} else None
    )
    candidate_version = next_candidate_version(
        db,
        project_id,
        "image",
        entity_type,
        entity_id,
        resolved_asset_role,
        resolved_variant_key,
    )
    generation_params, generation_seed, seed_strategy = _image_generation_params(
        task_id=task_id,
        target_kind="candidate",
        entity_type=entity_type,
        entity_id=entity_id,
        asset_role=resolved_asset_role,
        version=candidate_version,
        extra_params=extra_params,
    )
    reference_transport = _normalize_reference_transport(
        str(
            image_provider_selection.default_params.get(
                "reference_transport",
                settings.image_reference_transport,
            )
        )
    )
    resolution_payload = _asset_resolution_payload(asset_resolution, provider=provider)
    request_metadata = with_avd_asset_usage(
        {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "asset_role": resolved_asset_role,
            "variant_key": resolved_variant_key,
            "asset_reference_transport": reference_transport,
            "asset_resolution": resolution_payload,
            "candidate_policy": "pending_review_before_asset",
            "generation_seed": generation_seed,
            "seed_strategy": seed_strategy,
            "image_provider_profile_id": image_provider_selection.profile_id,
            "image_provider_profile_source": image_provider_selection.source,
        },
        asset_type="image",
        asset_role=resolved_asset_role,
        entity_type=entity_type,
        variant_key=resolved_variant_key,
    )
    references = (
        list(asset_resolution.references)
        if asset_resolution is not None and reference_transport in {"payload", "hybrid"}
        else []
    )
    return {
        "operation": operation,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "shot_id": entity_id if entity_type == "shot" else None,
        "asset_role": resolved_asset_role,
        "variant_key": resolved_variant_key,
        "candidate_version": candidate_version,
        "candidate_policy": "pending_review_before_asset",
        "source_asset_id": source_asset_id,
        "source_asset_version": source_asset_version,
        "source_candidate_id": source_candidate_id,
        "source_candidate_version": source_candidate_version,
        "image_provider_profile_id": image_provider_selection.profile_id,
        "asset_resolution": asset_resolution.to_task_payload() if asset_resolution else None,
        "provider_request": {
            "model": provider.model,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "references": references,
            "params": {
                **image_provider_selection.default_params,
                "asset_reference_transport": reference_transport,
                "asset_reference_metadata": (
                    dict(asset_resolution.reference_metadata)
                    if asset_resolution is not None
                    else {}
                ),
                **generation_params,
            },
            "metadata": request_metadata,
        },
        "request_metadata": request_metadata,
    }


def persist_image_candidate_task_result(
    db: Session,
    task: GenerationTask,
    response: ProviderResponse,
) -> AssetCandidate:
    """Persist one Provider result and complete its task in one short transaction."""
    if not begin_task_completion(db, task):
        existing = _completed_image_candidate(db, task)
        if existing is None:
            raise TaskCompletionConflictError(
                f"Succeeded image task {task.id} has no persisted candidate"
            )
        return existing

    payload = task.input_payload if isinstance(task.input_payload, dict) else {}
    request_payload = payload.get("provider_request")
    if not isinstance(request_payload, dict):
        raise ValueError("Image task provider request snapshot is missing")
    entity_type = str(payload.get("entity_type") or "")
    entity_id = str(payload.get("entity_id") or "")
    if not entity_type or not entity_id:
        raise ValueError("Image task target snapshot is missing")
    candidate = _persist_provider_image_candidate(
        db,
        project_id=task.project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        asset_role=str(payload.get("asset_role") or "") or None,
        variant_key=(
            str(payload.get("variant_key"))
            if payload.get("variant_key") is not None
            else None
        ),
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
        candidate_version=int(payload.get("candidate_version") or 1),
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

    mark_downstream_stages_pending(
        db,
        task.project_id,
        "images",
        summary="图片候选已更新，需要重新生成相关视频和导出",
    )
    if task.parent_task_id is None:
        mark_stage_ready(
            db,
            task.project_id,
            "images",
            task_id=task.id,
            summary="图片候选已生成，等待确认入库",
            metadata={
                "candidate_count": 1,
                "kind": str(payload.get("operation") or task.task_type),
                "candidate_id": candidate.id,
            },
        )
    mark_task_succeeded(
        db,
        task,
        result_payload={
            "candidate_ids": [candidate.id],
            "candidate_count": 1,
            "candidate_policy": "pending_review_before_asset",
            "generation_seeds": [
                int(payload.get("request_metadata", {}).get("generation_seed"))
            ]
            if isinstance(payload.get("request_metadata"), dict)
            and payload.get("request_metadata", {}).get("generation_seed") is not None
            else [],
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


def _completed_image_candidate(
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
        .where(AssetCandidate.asset_type == "image")
        .order_by(AssetCandidate.created_at, AssetCandidate.id)
        .limit(1)
    )


def _generate_image_asset(
    db: Session,
    *,
    project_id: str,
    task_id: str,
    entity_type: str,
    entity_id: str,
    asset_role: str | None = None,
    variant_key: str | None = None,
    prompt: str,
    negative_prompt: str | None,
    asset_resolution: AssetResolution | None = None,
    extra_params: dict[str, Any] | None = None,
    image_provider_selection: ImageProviderSelection | None = None,
) -> Asset:
    selection = image_provider_selection or resolve_image_provider_selection(db, None)
    provider = selection.provider
    resolved_asset_role = asset_role or infer_asset_role(asset_type="image", entity_type=entity_type, entity_id=entity_id)
    resolved_variant_key = variant_key or (
        "base" if entity_type in {"character", "scene", "prop"} else None
    )
    asset_version = next_asset_version(
        db,
        project_id,
        "image",
        entity_type,
        entity_id,
        resolved_asset_role,
        resolved_variant_key,
    )
    generation_params, generation_seed, seed_strategy = _image_generation_params(
        task_id=task_id,
        target_kind="asset",
        entity_type=entity_type,
        entity_id=entity_id,
        asset_role=resolved_asset_role,
        version=asset_version,
        extra_params=extra_params,
    )
    reference_transport = _normalize_reference_transport(
        str(selection.default_params.get("reference_transport", settings.image_reference_transport))
    )
    request_prompt = prompt
    references = asset_resolution.references if asset_resolution and reference_transport in {"payload", "hybrid"} else []
    request_negative_prompt = negative_prompt
    asset_resolution_payload = _asset_resolution_payload(asset_resolution, provider=provider)
    request_metadata = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "asset_role": resolved_asset_role,
        "variant_key": resolved_variant_key,
        "asset_reference_transport": reference_transport,
        "asset_resolution": asset_resolution_payload,
        "generation_seed": generation_seed,
        "seed_strategy": seed_strategy,
        "image_provider_profile_id": selection.profile_id,
        "image_provider_profile_source": selection.source,
    }
    request_metadata = with_avd_asset_usage(
        request_metadata,
        asset_type="image",
        asset_role=resolved_asset_role,
        entity_type=entity_type,
        variant_key=resolved_variant_key,
    )
    provider_response = provider.submit(
        ProviderRequest(
            project_id=project_id,
            task_id=f"{task_id}:image:{entity_type}:{entity_id}:{asset_version}",
            model=provider.model,
            prompt=request_prompt,
            negative_prompt=request_negative_prompt,
            references=references,
            params={
                **selection.default_params,
                "asset_reference_transport": reference_transport,
                "asset_reference_metadata": asset_resolution.reference_metadata if asset_resolution else {},
                **generation_params,
            },
            metadata=request_metadata,
        )
    )
    return _persist_provider_image_asset(
        db,
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        asset_role=resolved_asset_role,
        variant_key=resolved_variant_key,
        provider_name=provider.name,
        model=provider.model,
        prompt=request_prompt,
        negative_prompt=request_negative_prompt,
        response=provider_response,
        source_task_id=task_id,
        request_metadata=request_metadata,
    )


def _generate_image_candidate(
    db: Session,
    *,
    project_id: str,
    task_id: str,
    entity_type: str,
    entity_id: str,
    asset_role: str | None = None,
    variant_key: str | None = None,
    prompt: str,
    negative_prompt: str | None,
    asset_resolution: AssetResolution | None = None,
    extra_params: dict[str, Any] | None = None,
    image_provider_selection: ImageProviderSelection | None = None,
) -> AssetCandidate:
    selection = image_provider_selection or resolve_image_provider_selection(db, None)
    provider = selection.provider
    resolved_asset_role = asset_role or infer_asset_role(asset_type="image", entity_type=entity_type, entity_id=entity_id)
    resolved_variant_key = variant_key or (
        "base" if entity_type in {"character", "scene", "prop"} else None
    )
    candidate_version = next_candidate_version(
        db,
        project_id,
        "image",
        entity_type,
        entity_id,
        resolved_asset_role,
        resolved_variant_key,
    )
    generation_params, generation_seed, seed_strategy = _image_generation_params(
        task_id=task_id,
        target_kind="candidate",
        entity_type=entity_type,
        entity_id=entity_id,
        asset_role=resolved_asset_role,
        version=candidate_version,
        extra_params=extra_params,
    )
    reference_transport = _normalize_reference_transport(
        str(selection.default_params.get("reference_transport", settings.image_reference_transport))
    )
    request_prompt = prompt
    references = asset_resolution.references if asset_resolution and reference_transport in {"payload", "hybrid"} else []
    request_negative_prompt = negative_prompt
    asset_resolution_payload = _asset_resolution_payload(asset_resolution, provider=provider)
    request_metadata = {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "asset_role": resolved_asset_role,
        "variant_key": resolved_variant_key,
        "asset_reference_transport": reference_transport,
        "asset_resolution": asset_resolution_payload,
        "candidate_policy": "pending_review_before_asset",
        "generation_seed": generation_seed,
        "seed_strategy": seed_strategy,
        "image_provider_profile_id": selection.profile_id,
        "image_provider_profile_source": selection.source,
    }
    request_metadata = with_avd_asset_usage(
        request_metadata,
        asset_type="image",
        asset_role=resolved_asset_role,
        variant_key=resolved_variant_key,
        entity_type=entity_type,
    )
    provider_response = provider.submit(
        ProviderRequest(
            project_id=project_id,
            task_id=f"{task_id}:image-candidate:{entity_type}:{entity_id}:{candidate_version}",
            model=provider.model,
            prompt=request_prompt,
            negative_prompt=request_negative_prompt,
            references=references,
            params={
                **selection.default_params,
                "asset_reference_transport": reference_transport,
                "asset_reference_metadata": asset_resolution.reference_metadata if asset_resolution else {},
                **generation_params,
            },
            metadata=request_metadata,
        )
    )
    return _persist_provider_image_candidate(
        db,
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        asset_role=resolved_asset_role,
        variant_key=resolved_variant_key,
        provider_name=provider.name,
        model=provider.model,
        prompt=request_prompt,
        negative_prompt=request_negative_prompt,
        response=provider_response,
        source_task_id=task_id,
        request_metadata=request_metadata,
    )


def _persist_provider_image_asset(
    db: Session,
    *,
    project_id: str,
    entity_type: str,
    entity_id: str,
    asset_role: str | None = None,
    variant_key: str | None = None,
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
                "message": "Image provider failed",
                "provider": provider_name,
                "model": model,
            }
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)

    provider_asset = response.assets[0]
    resolved_asset_role = asset_role or infer_asset_role(asset_type="image", entity_type=entity_type, entity_id=entity_id)
    resolved_variant_key = variant_key or (
        "base" if entity_type in {"character", "scene", "prop"} else None
    )
    request_metadata = with_avd_asset_usage(
        request_metadata,
        asset_type="image",
        asset_role=resolved_asset_role,
        entity_type=entity_type,
        variant_key=resolved_variant_key,
    )
    version = next_asset_version(
        db,
        project_id,
        "image",
        entity_type,
        entity_id,
        resolved_asset_role,
        resolved_variant_key,
    )
    source_script_id, source_shot_batch_id = asset_source_context(db, entity_type, entity_id)
    source_stage_run_id = resolve_source_stage_run_id(db, project_id, source_task_id)
    asset_id = str(uuid4())
    asset_uri = materialize_data_uri(project_id, asset_id, provider_asset.uri, provider_asset.mime_type)
    consistency_check = _image_consistency_check(asset_uri, request_metadata)

    db.execute(
        update(Asset)
        .where(Asset.project_id == project_id)
        .where(Asset.asset_type == "image")
        .where(Asset.asset_role == resolved_asset_role)
        .where(Asset.entity_type == entity_type)
        .where(Asset.entity_id == entity_id)
        .where(Asset.variant_key == resolved_variant_key)
        .values(is_selected=False),
    )
    asset = Asset(
        id=asset_id,
        project_id=project_id,
        asset_type="image",
        asset_role=resolved_asset_role,
        entity_type=entity_type,
        entity_id=entity_id,
        variant_key=resolved_variant_key,
        source_task_id=source_task_id,
        completion_key=artifact_completion_key(
            source_task_id,
            artifact_kind="asset",
            asset_type="image",
            asset_role=resolved_asset_role,
            entity_type=entity_type,
            entity_id=entity_id,
            variant_key=resolved_variant_key,
        ),
        source_stage_run_id=source_stage_run_id,
        source_script_id=source_script_id,
        source_shot_batch_id=source_shot_batch_id,
        version=version,
        uri=asset_uri,
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
            "consistency_check": consistency_check,
        },
        status="ready_for_review",
        is_selected=True,
    )
    db.add(asset)
    db.flush()
    return asset


def _persist_provider_image_candidate(
    db: Session,
    *,
    project_id: str,
    entity_type: str,
    entity_id: str,
    asset_role: str | None = None,
    variant_key: str | None = None,
    provider_name: str,
    model: str,
    prompt: str,
    negative_prompt: str | None,
    response: ProviderResponse,
    source_task_id: str | None = None,
    request_metadata: dict | None = None,
    candidate_version: int | None = None,
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
                "message": "Image provider failed",
                "provider": provider_name,
                "model": model,
            }
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)

    provider_asset = response.assets[0]
    resolved_asset_role = asset_role or infer_asset_role(asset_type="image", entity_type=entity_type, entity_id=entity_id)
    request_metadata = with_avd_asset_usage(
        request_metadata,
        asset_type="image",
        asset_role=resolved_asset_role,
        entity_type=entity_type,
    )
    resolved_variant_key = variant_key or (
        "base" if entity_type in {"character", "scene", "prop"} else None
    )
    version = candidate_version or next_candidate_version(
        db,
        project_id,
        "image",
        entity_type,
        entity_id,
        resolved_asset_role,
        resolved_variant_key,
    )
    source_script_id, source_shot_batch_id = asset_source_context(db, entity_type, entity_id)
    source_stage_run_id = resolve_source_stage_run_id(db, project_id, source_task_id)
    candidate_id = str(uuid4())

    candidate_uri = materialize_data_uri(project_id, candidate_id, provider_asset.uri, provider_asset.mime_type)
    consistency_check = _image_consistency_check(candidate_uri, request_metadata)
    candidate = AssetCandidate(
        id=candidate_id,
        project_id=project_id,
        source_task_id=source_task_id,
        completion_key=artifact_completion_key(
            source_task_id,
            artifact_kind="candidate",
            candidate_type="generated",
            asset_type="image",
            asset_role=resolved_asset_role,
            entity_type=entity_type,
            entity_id=entity_id,
            variant_key=resolved_variant_key,
        ),
        source_stage_run_id=source_stage_run_id,
        source_script_id=source_script_id,
        source_shot_batch_id=source_shot_batch_id,
        candidate_type="generated",
        asset_type="image",
        asset_role=resolved_asset_role,
        entity_type=entity_type,
        entity_id=entity_id,
        variant_key=resolved_variant_key,
        version=version,
        uri=candidate_uri,
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
            "consistency_check": consistency_check,
        },
        status="pending_review",
    )
    db.add(candidate)
    db.flush()
    return candidate


def _resolve_state_variant(
    entity: Character | Scene | Prop,
    requested_key: str,
) -> tuple[str, str]:
    variant_key = requested_key.strip() or "base"
    if variant_key == "base":
        return "base", ""
    asset_spec = entity.asset_spec if isinstance(entity.asset_spec, dict) else {}
    variants = asset_spec.get("state_variants")
    if not isinstance(variants, list):
        variants = []
    for item in variants:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key != variant_key:
            continue
        description = str(item.get("description") or item.get("name") or "").strip()
        if not description:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"State variant {variant_key} has no description",
            )
        return variant_key, description
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"Unknown state variant: {variant_key}",
    )


def _prompt_with_state_variant(
    db: Session,
    *,
    entity_type: str,
    entity_id: str,
    variant_key: str | None,
    prompt: str,
) -> str:
    if not variant_key or variant_key == "base":
        return prompt
    model_by_type = {
        "character": Character,
        "scene": Scene,
        "prop": Prop,
    }
    model = model_by_type.get(entity_type)
    entity = db.get(model, entity_id) if model is not None else None
    if entity is None:
        return prompt
    _, description = _resolve_state_variant(entity, variant_key)
    suffix = f"剧情状态变体：{description}"
    return prompt if suffix in prompt else f"{prompt}；{suffix}"


def _assert_resource_task_available(
    db: Session,
    project_id: str,
    *,
    resource_type: str,
    resource_id: str,
) -> None:
    active_tasks = db.scalars(
        select(GenerationTask)
        .where(GenerationTask.project_id == project_id)
        .where(GenerationTask.status.in_(("queued", "running")))
    ).all()
    for task in active_tasks:
        payload = task.input_payload if isinstance(task.input_payload, dict) else {}
        bound_ids = {
            str(payload.get("entity_id") or ""),
            str(payload.get("shot_id") or ""),
        }
        bound_ids.update(
            str(item)
            for key in ("character_ids", "scene_ids", "prop_ids", "shot_ids")
            for item in (payload.get(key) if isinstance(payload.get(key), list) else [])
        )
        if resource_id not in bound_ids:
            continue
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resource_generation_in_progress",
                "message": f"{resource_type} {resource_id} 已有生成任务在运行",
                "task_id": task.id,
            },
        )


def _resolve_image_asset_generation_context(
    db: Session,
    asset: Asset,
    *,
    prompt_data: dict[str, Any] | None = None,
):
    return _resolve_image_generation_context_for_target(
        db,
        project_id=asset.project_id,
        entity_type=asset.entity_type,
        entity_id=asset.entity_id,
        asset_role=asset.asset_role,
        prompt_data=prompt_data,
    )


def _resolve_image_generation_context_for_target(
    db: Session,
    *,
    project_id: str,
    entity_type: str | None,
    entity_id: str | None,
    asset_role: str | None = None,
    prompt_data: dict[str, Any] | None = None,
):
    if entity_type in {"character", "scene", "prop"}:
        return resolve_generation_assets(db, project_id, stage="reference_image")
    if entity_type == "shot":
        shot = db.get(Shot, entity_id or "")
        if shot:
            return resolve_generation_assets(
                db,
                project_id,
                stage="shot_image",
                shot=shot,
                visible_character_ids=(prompt_data or {}).get("visible_character_ids"),
                visible_prop_ids=(prompt_data or {}).get("visible_prop_ids"),
            )
    return None


def _stored_image_prompt_for_target(
    db: Session,
    *,
    project_id: str,
    entity_type: str,
    entity_id: str,
    asset_role: str | None,
) -> dict[str, Any]:
    if not asset_role:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="图片资产缺少 asset_role")
    if asset_role.startswith("shot_frame_") or entity_type == "shot_frame":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="历史帧图只读；当前工作流只生成 shot_storyboard，用户可明确选择是否将其作为视频首帧",
        )
    project = db.get(Project, project_id)
    if entity_type == "character":
        entity = db.get(Character, entity_id)
        if entity is not None:
            return {
                "positive_prompt": character_reference_prompt(entity, _visual_style(project), asset_role),
                "negative_prompt": reference_negative_prompt(entity, asset_role),
                "visible_character_ids": [],
                "visible_prop_ids": [],
            }
    if entity_type == "scene":
        entity = db.get(Scene, entity_id)
        if entity is not None:
            return {
                "positive_prompt": scene_reference_prompt(entity, _visual_style(project)),
                "negative_prompt": reference_negative_prompt(entity, asset_role),
                "visible_character_ids": [],
                "visible_prop_ids": [],
            }
    if entity_type == "prop":
        entity = db.get(Prop, entity_id)
        if entity is not None:
            return {
                "positive_prompt": prop_reference_prompt(entity, _visual_style(project)),
                "negative_prompt": reference_negative_prompt(entity, asset_role),
                "visible_character_ids": [],
                "visible_prop_ids": [],
            }
    if entity_type == "shot":
        shot = db.get(Shot, entity_id)
        if shot is not None:
            return _shot_image_prompt_data(
                shot,
                asset_role,
                style=_visual_style(project),
            )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="当前图片目标缺少可直接生成的 Prompt；请重新生成分镜或补全已有字段",
    )


def _visual_style(project: Project | None) -> str:
    if project is None:
        return resolve_visual_style(None)
    return resolve_visual_style(project.style, project.creative_settings)


def _shot_image_prompt_data(
    shot: Shot,
    asset_role: str,
    *,
    style: str | None = None,
) -> dict[str, Any]:
    if asset_role != "shot_storyboard":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="镜头图片只支持 shot_storyboard；独立帧图已退出生产链路",
        )
    key = "shot_storyboard"
    card = shot.shot_card if isinstance(shot.shot_card, dict) else {}
    prompts = card.get("image_prompts") if isinstance(card.get("image_prompts"), dict) else {}
    item = prompts.get(key) if isinstance(prompts.get(key), dict) else {}
    positive = item.get("positive_prompt")
    if key == "shot_storyboard" and (not isinstance(positive, str) or not positive.strip()):
        positive = shot.image_prompt
    if not isinstance(positive, str) or not positive.strip():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"镜头 {shot.shot_no} 缺少 {key} Prompt；系统不会再运行旧图片编译器",
        )
    negative = item.get("negative_prompt")
    if key == "shot_storyboard" and not negative:
        negative = shot.negative_prompt
    return {
        "positive_prompt": compose_image_prompt(positive.strip(), style),
        "negative_prompt": str(negative).strip() if negative else None,
        "visible_character_ids": _bounded_prompt_ids(item.get("visible_character_ids"), shot.character_ids),
        "visible_prop_ids": _bounded_prompt_ids(item.get("visible_prop_ids"), shot.prop_ids),
    }


def _bounded_prompt_ids(value: Any, allowed: list) -> list[str]:
    allowed_ids = list(dict.fromkeys(item for item in allowed if isinstance(item, str)))
    if not isinstance(value, list):
        return allowed_ids
    allowed_set = set(allowed_ids)
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item in allowed_set))


def _candidate_image_provider_profile_id(candidate: AssetCandidate) -> str | None:
    raw_response = candidate.raw_response if isinstance(candidate.raw_response, dict) else {}
    request_metadata = raw_response.get("request_metadata")
    if not isinstance(request_metadata, dict):
        return None
    profile_id = request_metadata.get("image_provider_profile_id")
    return str(profile_id).strip() if profile_id else None


def _normalize_reference_transport(value: str | None) -> str:
    normalized = (value or "prompt").strip().lower()
    if normalized in {"none", "prompt", "payload", "hybrid"}:
        return normalized
    return "prompt"


def _image_candidate_count() -> int:
    # Candidate quantity is a workflow/versioning policy, not a provider model
    # parameter. One click creates one candidate per target; a later explicit
    # regeneration creates the next version with a new app-side seed.
    return 1


def _image_generation_params(
    *,
    task_id: str,
    target_kind: str,
    entity_type: str,
    entity_id: str,
    asset_role: str | None,
    version: int,
    extra_params: dict[str, Any] | None,
) -> tuple[dict[str, Any], int, str]:
    params = dict(extra_params or {})
    explicit_seed = params.get("seed")
    if explicit_seed is not None:
        generation_seed = int(explicit_seed)
        seed_strategy = "explicit"
    else:
        seed_namespace = int(settings.image_seed or 0)
        seed_material = "\x1f".join(
            [
                str(seed_namespace),
                task_id,
                target_kind,
                entity_type,
                entity_id,
                asset_role or "",
                str(version),
            ]
        )
        digest = blake2b(seed_material.encode("utf-8"), digest_size=8, person=b"avd-image-seed").digest()
        # Keep generated seeds in the positive signed 32-bit range accepted by
        # the broadest set of diffusion APIs. The task id still makes every
        # explicit regeneration click produce a different value.
        generation_seed = int.from_bytes(digest, "big") % ((1 << 31) - 1) + 1
        seed_strategy = "task_scoped_v1"
    params["seed"] = generation_seed
    return params, generation_seed, seed_strategy


def _asset_resolution_payload(asset_resolution: AssetResolution | None, *, provider) -> dict[str, Any] | None:
    if asset_resolution is None:
        return None
    payload = asset_resolution.to_task_payload()
    payload["provider"] = {
        "name": provider.name,
        "model": provider.model,
        "capabilities": list(provider.capabilities),
    }
    return payload


def _image_consistency_check(uri: str, request_metadata: dict | None) -> dict[str, Any] | None:
    asset_resolution = request_metadata.get("asset_resolution") if isinstance(request_metadata, dict) else None
    if not isinstance(asset_resolution, dict):
        return None
    return evaluate_image_consistency(image_uri=uri, asset_resolution=asset_resolution)


def _build_grid_image_prompt(
    shots: list[Shot],
    rows: int,
    cols: int,
    *,
    style: str,
) -> str:
    cell_count = rows * cols
    cells = []
    for index, shot in enumerate(shots, start=1):
        cells.append(
            f"画格 {index}：{shot.image_prompt or shot.description}；"
            f"{professional_shot_size(shot.camera_shot or '中景')}，{professional_movement(shot.camera_movement or '固定机位')}。"
        )
    subject_prompt = (
        f"{rows}x{cols} 分镜宫格，必须正好包含 {cell_count} 个清晰画格，不合并画格，不缺失画格。"
        "每个画格遵循项目画幅，角色、场景和道具保持一致，"
        "高质量，无字幕，无乱码文字，无水印。\n"
        + "\n".join(cells)
    )
    return compose_image_prompt(subject_prompt, style)


def _persist_grid_cell_assets(
    db: Session,
    *,
    project_id: str,
    grid_asset: Asset,
    shots: list[Shot],
    rows: int,
    cols: int,
    style: str,
) -> list[Asset]:
    data_uri = grid_asset.uri if grid_asset.uri.startswith("data:image/") else None
    if data_uri:
        split_uris = _split_grid_data_uri(data_uri, rows, cols)
    else:
        split_uris = [f"mock://grid-cells/{grid_asset.id}/{index + 1}.png" for index in range(rows * cols)]

    assets: list[Asset] = []
    for shot, uri in zip(shots, split_uris, strict=False):
        prompt_data = _shot_image_prompt_data(
            shot,
            "shot_storyboard",
            style=style,
        )
        db.execute(
            update(Asset)
            .where(Asset.project_id == project_id)
            .where(Asset.asset_type == "image")
            .where(Asset.entity_type == "shot")
            .where(Asset.entity_id == shot.id)
            .where(Asset.asset_role == "shot_storyboard")
            .values(is_selected=False),
        )
        asset = Asset(
            project_id=project_id,
            asset_type="image",
            asset_role="shot_storyboard",
            entity_type="shot",
            entity_id=shot.id,
            source_task_id=grid_asset.source_task_id,
            completion_key=artifact_completion_key(
                grid_asset.source_task_id,
                artifact_kind="asset",
                asset_type="image",
                asset_role="shot_storyboard",
                entity_type="shot",
                entity_id=shot.id,
            ),
            source_script_id=shot.script_id,
            source_shot_batch_id=shot.shot_batch_id,
            version=next_asset_version(db, project_id, "image", "shot", shot.id, "shot_storyboard"),
            uri=uri,
            mime_type="image/png",
            provider=grid_asset.provider,
            model=grid_asset.model,
            prompt=prompt_data["positive_prompt"],
            negative_prompt=prompt_data["negative_prompt"],
            raw_response={
                "source_grid_asset_id": grid_asset.id,
                "mode": "grid_split",
                "rows": rows,
                "cols": cols,
                "request_metadata": with_avd_asset_usage(
                    {"source_grid_asset_id": grid_asset.id},
                    asset_type="image",
                    asset_role="shot_storyboard",
                    entity_type="shot",
                ),
            },
            status="ready_for_review",
            is_selected=True,
        )
        db.add(asset)
        db.flush()
        assets.append(asset)
    return assets


def _split_grid_data_uri(data_uri: str, rows: int, cols: int) -> list[str]:
    try:
        from PIL import Image
    except ImportError:
        return []

    header, payload = data_uri.split(",", 1)
    image_bytes = base64.b64decode(payload)
    image = Image.open(BytesIO(image_bytes))
    cell_width = image.width // cols
    cell_height = image.height // rows
    out_dir = Path(settings.storage_root).resolve() / "assets" / "grid-cells"
    out_dir.mkdir(parents=True, exist_ok=True)

    uris = []
    for row in range(rows):
        for col in range(cols):
            cell = image.crop(
                (
                    col * cell_width,
                    row * cell_height,
                    (col + 1) * cell_width,
                    (row + 1) * cell_height,
                )
            )
            filename = f"grid_cell_{datetime.now(timezone.utc).timestamp():.0f}_{row}_{col}.png"
            path = out_dir / filename
            cell.save(path, "PNG")
            uris.append(f"{settings.public_storage_base_url}/assets/grid-cells/{filename}")
    return uris
