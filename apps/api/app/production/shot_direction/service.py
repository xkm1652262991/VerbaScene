from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agents.shot_breakdown import segment_planning_contract
from app.agents.style import resolve_visual_style
from app.core.config import settings
from app.models import Asset, Character, Dialogue, GenerationTask, Project, Prop, Scene, Script, Shot
from app.platform.media import get_media_store
from app.platform.tasks.repository import TaskConflictError, TaskRepository
from app.platform.tasks.types import TaskExecutionError
from app.providers.defaults import provider_registry
from app.providers.types import ProviderType, SubmissionState
from app.production.shot_direction.asset_inspector import MetadataAssetInspector
from app.production.shot_direction.contracts import (
    SHOT_DIRECTION_PIPELINE_VERSION,
    SHOT_DIRECTION_TASK_TYPE,
    ShotDirectionInput,
)
from app.production.shot_direction.pipeline import (
    ShotDirectionPipelineFailure,
    ShotDirectionPipelineResult,
    run_shot_direction_pipeline,
)
from app.production.shot_direction.prompts import shot_direction_system_prompt
from app.production.shot_prompt_compiler import (
    apply_video_prompt_field,
    select_entities,
    select_scene,
    shot_prompt_fingerprint,
)
from app.services.agent_config_service import resolve_agent_config
from app.services.entity_service import get_current_entities
from app.services.failure_reason_service import normalize_failure_reason
from app.services.shot_service import get_latest_approved_script, get_project_or_404
from app.services.stage_run_service import finish_latest_stage_run
from app.services.task_service import mark_task_failed, mark_task_running, mark_task_succeeded, update_task_progress
from app.services.workflow_state_service import mark_downstream_stages_pending, mark_stage_ready, mark_stage_running


def create_shot_direction_task(
    db: Session,
    project_id: str,
    *,
    idempotency_key: str | None = None,
) -> GenerationTask:
    normalized_idempotency_key = (idempotency_key or "").strip()
    if normalized_idempotency_key:
        existing = db.scalar(
            select(GenerationTask)
            .where(GenerationTask.project_id == project_id)
            .where(GenerationTask.task_type == SHOT_DIRECTION_TASK_TYPE)
            .where(GenerationTask.idempotency_key == normalized_idempotency_key)
        )
        if existing is not None:
            return existing

    project = get_project_or_404(db, project_id)
    script = get_latest_approved_script(db, project_id)
    characters, scenes, props = get_current_entities(db, project_id)
    if not characters or not scenes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="生成片段方案前，请先从剧本提取角色和场景设定",
        )
    dialogues = _dialogues_for_script(db, script)
    adopted_assets = _adopted_image_assets(db, project_id)
    source = _shot_direction_input_from_records(
        project=project,
        script=script,
        characters=characters,
        scenes=scenes,
        props=props,
        dialogues=dialogues,
        adopted_assets=adopted_assets,
    )
    asset_report = MetadataAssetInspector(get_media_store()).inspect(source)
    if not asset_report["planning_ready"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="结构化角色或场景不完整，无法创建分镜导演任务",
        )

    draft_config = resolve_agent_config(
        db,
        agent_type="storyboard_breaker",
        default_provider=settings.llm_provider,
        default_model=settings.llm_model,
    )
    provider = provider_registry.get(ProviderType.LLM, draft_config.provider)
    model = draft_config.model or provider.model
    review_config = resolve_agent_config(
        db,
        agent_type="storyboard_reviewer",
        default_provider=provider.name,
        default_model=model,
    )
    review_provider = provider_registry.get(ProviderType.LLM, review_config.provider)
    review_model = review_config.model or review_provider.model
    temperatures = {
        "storyboard_draft": _phase_temperature(draft_config, "storyboard_draft", 0.4),
        "storyboard_reflection": _phase_temperature(review_config, "storyboard_reflection", 0.2),
        "storyboard_patch": _phase_temperature(draft_config, "storyboard_patch", 0.3),
        "structure_recovery": _phase_temperature(draft_config, "structure_recovery", 0.1),
    }
    input_payload = {
        "pipeline_version": SHOT_DIRECTION_PIPELINE_VERSION,
        "script_id": script.id,
        "source_script_id": script.id,
        "character_ids": [item.id for item in characters],
        "scene_ids": [item.id for item in scenes],
        "prop_ids": [item.id for item in props],
        "reference_asset_ids": [item.id for item in adopted_assets],
        "shot_direction_input": source.to_payload(),
        "system_prompt": shot_direction_system_prompt(draft_config.system_prompt),
        "review_runtime": {
            "provider": review_provider.name,
            "model": review_model,
            "system_prompt": shot_direction_system_prompt(review_config.system_prompt),
        },
        "temperatures": temperatures,
    }
    try:
        creation = TaskRepository().create(
            db,
            project_id=project_id,
            task_type=SHOT_DIRECTION_TASK_TYPE,
            resource_key=f"project:{project_id}:shots",
            idempotency_key=normalized_idempotency_key or None,
            provider=provider.name,
            model=model,
            input_payload=input_payload,
            max_retries=1,
            progress_label="等待分镜导演 Agent",
            raw_response={
                "checkpoint": {
                    "pipeline_version": SHOT_DIRECTION_PIPELINE_VERSION,
                    "asset_report": asset_report,
                    "responses": {},
                    "pipeline_trace": {
                        "phases": [
                            {
                                "phase": "asset_observation",
                                "status": "succeeded",
                                "inspection_level": "metadata_only",
                            }
                        ],
                        "fallbacks": [],
                    },
                }
            },
        )
    except TaskConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "该项目已有分镜导演任务正在执行", "task_id": exc.task_id},
        ) from exc
    db.commit()
    task = creation.task
    db.refresh(task)
    return task


def execute_shot_direction_task(db: Session, task_id: str) -> tuple[list[Shot], GenerationTask]:
    task = db.get(GenerationTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.task_type != SHOT_DIRECTION_TASK_TYPE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该任务不属于分镜导演队列")
    if task.status == "succeeded":
        return _shots_for_batch(db, task.project_id, task.id), task
    if task.status not in {"queued", "running"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该分镜导演任务不可执行")

    source = ShotDirectionInput.from_payload(task.input_payload)
    project = db.get(Project, source.project_id)
    script = db.get(Script, source.script_id)
    if project is None or script is None or script.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="分镜任务的冻结项目或剧本已不存在")

    provider = provider_registry.get(ProviderType.LLM, task.provider)
    model = task.model or provider.model
    review_runtime = (
        task.input_payload.get("review_runtime")
        if isinstance(task.input_payload.get("review_runtime"), dict)
        else {}
    )
    review_provider = provider_registry.get(
        ProviderType.LLM,
        str(review_runtime.get("provider") or task.provider),
    )
    review_model = str(review_runtime.get("model") or model)
    raw_response = task.raw_response if isinstance(task.raw_response, dict) else {}
    checkpoint = raw_response.get("checkpoint") if isinstance(raw_response.get("checkpoint"), dict) else {}
    asset_report = (
        checkpoint.get("asset_report")
        if isinstance(checkpoint.get("asset_report"), dict)
        else MetadataAssetInspector(get_media_store()).inspect(source)
    )

    mark_stage_running(db, project.id, "shots", task_id=task.id)
    mark_task_running(db, task, "正在恢复或启动分镜导演流水线", max(5, task.progress))

    def persist_checkpoint(value: dict[str, Any], label: str, progress: int) -> None:
        previous_raw_response = dict(task.raw_response or {})
        db.refresh(task)
        if task.cancel_requested_at is not None or task.status == "cancelling":
            raise TaskExecutionError("task_cancelled", "分镜导演任务已请求取消")
        task.raw_response = {"checkpoint": value}
        task.provider_task_id = _last_provider_task_id(value)
        update_task_progress(db, task, label, progress)
        if task.cancel_requested_at is not None or task.status == "cancelling":
            inflight = value.get("inflight_phase")
            if (
                isinstance(inflight, dict)
                and inflight.get("submission_state") == SubmissionState.NOT_SUBMITTED.value
            ):
                task.raw_response = previous_raw_response
                db.add(task)
                db.commit()
            raise TaskExecutionError("task_cancelled", "分镜导演任务已请求取消")

    try:
        pipeline_result = run_shot_direction_pipeline(
            provider=provider,
            review_provider=review_provider,
            source=source,
            asset_report=asset_report,
            task_id=task.id,
            model=model,
            review_model=review_model,
            system_prompt=str(task.input_payload.get("system_prompt") or ""),
            review_system_prompt=str(
                review_runtime.get("system_prompt") or task.input_payload.get("system_prompt") or ""
            ),
            temperatures=_task_temperatures(task.input_payload),
            retry_not_submitted=task.retry_count < task.max_retries,
            checkpoint=checkpoint,
            on_checkpoint=persist_checkpoint,
        )
    except TaskExecutionError as exc:
        if exc.code == "task_cancelled":
            finish_latest_stage_run(
                db,
                task.project_id,
                "production",
                status="cancelled",
                task_id=task.id,
                error_code="shot_direction_cancelled",
                error_message="分镜导演任务已取消",
            )
            db.commit()
        raise
    except ShotDirectionPipelineFailure as exc:
        if (
            exc.retryable
            and exc.submission_state == SubmissionState.NOT_SUBMITTED
            and task.retry_count < task.max_retries
        ):
            retry_checkpoint = _retry_checkpoint(exc)
            task.raw_response = {"checkpoint": retry_checkpoint}
            task.provider_task_id = _last_provider_task_id(retry_checkpoint)
            db.add(task)
            db.commit()
            raise TaskExecutionError(
                code=exc.code,
                message=exc.message,
                retryable=True,
                submission_state=SubmissionState.NOT_SUBMITTED,
                raw_response={"checkpoint": retry_checkpoint},
            ) from exc

        failure_code = (
            "provider_submission_uncertain"
            if exc.submission_state == SubmissionState.UNKNOWN
            else exc.code
        )
        failure_raw = {
            "checkpoint": exc.checkpoint,
            "failure_phase": exc.phase,
            "submission_state": exc.submission_state.value,
        }
        failure_reason = normalize_failure_reason(
            code=failure_code,
            message=exc.message,
            raw_response=failure_raw,
            stage="shots",
            task_type=task.task_type,
            provider=task.provider,
            retryable=exc.retryable,
        )
        mark_task_failed(
            db,
            task,
            code=failure_code,
            message=exc.message,
            raw_response=failure_raw,
            provider_task_id=exc.provider_task_id,
            failure_reason=failure_reason,
        )
        finish_latest_stage_run(
            db,
            project.id,
            "production",
            status="failed",
            task_id=task.id,
            output_payload=failure_raw,
            error_code=failure_code,
            error_message=exc.message,
            failure_reason=failure_reason,
        )
        db.commit()
        raise

    db.refresh(task)
    if task.cancel_requested_at is not None or task.status == "cancelling":
        finish_latest_stage_run(
            db,
            task.project_id,
            "production",
            status="cancelled",
            task_id=task.id,
            error_code="shot_direction_cancelled",
            error_message="分镜导演任务已取消",
        )
        db.commit()
        raise TaskExecutionError("task_cancelled", "分镜导演任务已请求取消")

    shot_dicts = _compile_final_shots(source, pipeline_result)
    db.execute(
        update(Shot)
        .where(Shot.project_id == source.project_id)
        .where(Shot.is_current.is_(True))
        .values(is_current=False)
    )
    db.flush()
    mark_downstream_stages_pending(
        db,
        source.project_id,
        "shots",
        summary="分镜批次已更新，需要重新检查视频与导出",
    )
    shots = [
        Shot(
            project_id=source.project_id,
            script_id=source.script_id,
            shot_batch_id=task.id,
            is_current=True,
            status="ready_for_review",
            **item,
        )
        for item in shot_dicts
    ]
    db.add_all(shots)
    db.flush()
    _bind_frozen_dialogues(db, source, shots)
    _write_prompt_fingerprints(source, shots)

    director_report = _director_report(pipeline_result)
    task.raw_response = {"checkpoint": pipeline_result.checkpoint}
    result_payload = {
        "shot_ids": [shot.id for shot in shots],
        "shot_count": len(shots),
        "planned_duration_sec": sum(float(shot.duration_sec or 0) for shot in shots),
        "shot_batch_id": task.id,
        "pipeline_version": SHOT_DIRECTION_PIPELINE_VERSION,
        "quality_gate": pipeline_result.quality_gate,
        "planning_ready": bool(pipeline_result.asset_report.get("planning_ready")),
        "generation_ready": bool(pipeline_result.asset_report.get("generation_ready")),
        "inspection_level": str(pipeline_result.asset_report.get("inspection_level") or "metadata_only"),
        "issue_counts": pipeline_result.issue_counts,
        "patch_applied": pipeline_result.patch_applied,
        "patched_shot_nos": pipeline_result.patched_shot_nos,
        "resolved_issue_codes": pipeline_result.resolved_issue_codes,
        "unresolved_issue_codes": pipeline_result.unresolved_issue_codes,
        "contract_error_codes": [
            str(item.get("code"))
            for item in pipeline_result.contract_report.get("errors") or []
            if isinstance(item, dict) and item.get("code")
        ],
        "asset_gaps": deepcopy(pipeline_result.asset_report.get("missing_references") or []),
        "asset_conflicts": deepcopy(pipeline_result.asset_report.get("conflicts") or []),
        "review_provider": review_provider.name,
        "review_model": review_model,
        "director_report": director_report,
    }
    mark_task_succeeded(
        db,
        task,
        output_asset_ids=[],
        result_payload=result_payload,
        raw_response=task.raw_response,
        provider_task_id=_last_provider_task_id(pipeline_result.checkpoint),
    )
    mark_stage_ready(
        db,
        source.project_id,
        "shots",
        task_id=task.id,
        summary=(
            f"{len(shots)} 个视频片段方案已生成，可继续编辑"
            if pipeline_result.quality_gate == "pass"
            else f"{len(shots)} 个视频片段方案已生成，建议查看导演检查"
        ),
        metadata={
            "shot_count": len(shots),
            "quality_gate": pipeline_result.quality_gate,
            "generation_ready": bool(pipeline_result.asset_report.get("generation_ready")),
        },
    )
    db.add(task)
    db.commit()
    for item in [*shots, task]:
        db.refresh(item)
    return _shots_for_batch(db, source.project_id, task.id), task


def generate_shots_now(db: Session, project_id: str) -> tuple[list[Shot], GenerationTask]:
    """Synchronous helper for tests and maintenance commands, never used by HTTP."""

    task = create_shot_direction_task(db, project_id)
    return execute_shot_direction_task(db, task.id)


def cancel_shot_direction_stage(db: Session, task_id: str) -> None:
    task = db.get(GenerationTask, task_id)
    if task is None or task.task_type != SHOT_DIRECTION_TASK_TYPE:
        return
    finish_latest_stage_run(
        db,
        task.project_id,
        "production",
        status="cancelled",
        task_id=task.id,
        error_code="shot_direction_cancelled",
        error_message="分镜导演任务已取消",
    )
    db.commit()


def _shot_direction_input_from_records(
    *,
    project: Project,
    script: Script,
    characters: list[Character],
    scenes: list[Scene],
    props: list[Prop],
    dialogues: list[Dialogue],
    adopted_assets: list[Asset],
) -> ShotDirectionInput:
    visual_style = resolve_visual_style(project.style, project.creative_settings)
    return ShotDirectionInput(
        project_id=project.id,
        script_id=script.id,
        title=project.title,
        style=visual_style,
        target_duration_sec=int(project.target_duration_sec or 90),
        aspect_ratio=project.aspect_ratio,
        resolution=project.resolution,
        creative_settings=deepcopy(project.creative_settings or {}),
        script_content=script.content,
        script_scenes=deepcopy(script.scenes if isinstance(script.scenes, list) else []),
        dialogues=[_dialogue_payload(item) for item in dialogues],
        characters=[_character_payload(item) for item in characters],
        scenes=[_scene_payload(item) for item in scenes],
        props=[_prop_payload(item) for item in props],
        adopted_assets=[_asset_payload(item) for item in adopted_assets],
        segment_contract=segment_planning_contract(project.target_duration_sec or 90),
    )


def _compile_final_shots(
    source: ShotDirectionInput,
    pipeline_result: ShotDirectionPipelineResult,
) -> list[dict[str, Any]]:
    project = source.project_value()
    characters = source.character_values()
    scenes = source.scene_values()
    props = source.prop_values()
    dialogue_by_id = {item.id: item for item in source.dialogue_values()}
    result: list[dict[str, Any]] = []
    for raw in pipeline_result.final:
        item = deepcopy(raw)
        image_prompts = item.pop("image_prompts", None)
        card = dict(item.get("shot_card") or {})
        if isinstance(image_prompts, dict):
            card["image_prompts"] = image_prompts
        card["image_prompt_flow"] = "storyboard_first_frame_direct_v1"
        item["shot_card"] = card
        storyboard = (card.get("image_prompts") or {}).get("shot_storyboard") or {}
        item["image_prompt"] = str(storyboard.get("positive_prompt") or "")
        item["negative_prompt"] = str(storyboard.get("negative_prompt") or "")
        selected_dialogues = [
            dialogue_by_id[dialogue_id]
            for dialogue_id in item.get("dialogue_ids") or []
            if dialogue_id in dialogue_by_id
        ]
        apply_video_prompt_field(
            project=project,
            data=item,
            characters=characters,
            scenes=scenes,
            props=props,
            dialogues=selected_dialogues,
        )
        result.append(item)
    return result


def _bind_frozen_dialogues(db: Session, source: ShotDirectionInput, shots: list[Shot]) -> None:
    dialogue_ids = sorted(source.allowed_dialogue_ids)
    if not dialogue_ids:
        return
    rows = list(
        db.scalars(
            select(Dialogue)
            .where(Dialogue.project_id == source.project_id)
            .where(Dialogue.script_id == source.script_id)
            .where(Dialogue.id.in_(dialogue_ids))
        ).all()
    )
    by_id = {row.id: row for row in rows}
    for row in rows:
        row.shot_id = None
        db.add(row)
    for shot in shots:
        for dialogue_id in shot.dialogue_ids or []:
            row = by_id.get(str(dialogue_id))
            if row is not None:
                row.shot_id = shot.id
                db.add(row)


def _write_prompt_fingerprints(source: ShotDirectionInput, shots: list[Shot]) -> None:
    project = source.project_value()
    characters = source.character_values()
    scenes = source.scene_values()
    props = source.prop_values()
    dialogue_by_id = {item.id: item for item in source.dialogue_values()}
    for shot in shots:
        selected_dialogues = [
            dialogue_by_id[dialogue_id]
            for dialogue_id in shot.dialogue_ids or []
            if dialogue_id in dialogue_by_id
        ]
        card = dict(shot.shot_card or {})
        card["prompt_fingerprint"] = shot_prompt_fingerprint(
            project=project,
            shot=shot,
            characters=characters,
            scene=select_scene(scenes, shot.scene_id),
            props=select_entities(props, shot.prop_ids),
            dialogues=selected_dialogues,
        )
        card["prompt_stale"] = False
        shot.shot_card = card


def _director_report(result: ShotDirectionPipelineResult) -> dict[str, Any]:
    unresolved = set(result.unresolved_issue_codes)
    review_issues = [
        deepcopy(item)
        for item in result.review.get("issues") or []
        if isinstance(item, dict)
    ]
    return {
        "pipeline_version": SHOT_DIRECTION_PIPELINE_VERSION,
        "quality_gate": result.quality_gate,
        "asset_readiness": deepcopy(result.asset_report),
        "reflection": {
            "summary": str(result.review.get("summary") or ""),
            "issues": review_issues,
            "issue_counts": result.issue_counts,
        },
        "patch": {
            "applied": result.patch_applied,
            "patched_shot_nos": result.patched_shot_nos,
            "resolved_issue_codes": result.resolved_issue_codes,
            "unresolved_issue_codes": result.unresolved_issue_codes,
        },
        "contract": deepcopy(result.contract_report),
        "unresolved_issues": [
            item for item in review_issues if str(item.get("code") or "") in unresolved
        ],
    }


def _dialogues_for_script(db: Session, script: Script) -> list[Dialogue]:
    return list(
        db.scalars(
            select(Dialogue)
            .where(Dialogue.project_id == script.project_id)
            .where(Dialogue.script_id == script.id)
            .order_by(Dialogue.sequence_order, Dialogue.created_at)
        ).all()
    )


def _adopted_image_assets(db: Session, project_id: str) -> list[Asset]:
    return list(
        db.scalars(
            select(Asset)
            .where(Asset.project_id == project_id)
            .where(Asset.asset_type == "image")
            .where(Asset.is_selected.is_(True))
            .order_by(Asset.entity_type, Asset.entity_id, Asset.asset_role, Asset.version.desc())
        ).all()
    )


def _shots_for_batch(db: Session, project_id: str, batch_id: str) -> list[Shot]:
    return list(
        db.scalars(
            select(Shot)
            .where(Shot.project_id == project_id)
            .where(Shot.shot_batch_id == batch_id)
            .order_by(Shot.shot_no, Shot.created_at)
        ).all()
    )


def _phase_temperature(config: Any, phase: str, fallback: float) -> float:
    settings_payload = config.settings if isinstance(config.settings, dict) else {}
    candidate = settings_payload.get(f"{phase}_temperature")
    if candidate is None and phase in {"storyboard_draft", "storyboard_reflection"}:
        candidate = config.temperature
    try:
        return max(0.0, min(2.0, float(candidate)))
    except (TypeError, ValueError):
        return fallback


def _task_temperatures(payload: dict[str, Any]) -> dict[str, float]:
    value = payload.get("temperatures") if isinstance(payload.get("temperatures"), dict) else {}
    defaults = {
        "storyboard_draft": 0.4,
        "storyboard_reflection": 0.2,
        "storyboard_patch": 0.3,
        "structure_recovery": 0.1,
    }
    return {phase: float(value.get(phase, fallback)) for phase, fallback in defaults.items()}


def _last_provider_task_id(checkpoint: dict[str, Any]) -> str | None:
    trace = checkpoint.get("pipeline_trace") if isinstance(checkpoint, dict) else None
    phases = trace.get("phases") if isinstance(trace, dict) else None
    if not isinstance(phases, list):
        return None
    for item in reversed(phases):
        if isinstance(item, dict) and item.get("provider_task_id"):
            return str(item["provider_task_id"])
    return None


def _retry_checkpoint(exc: ShotDirectionPipelineFailure) -> dict[str, Any]:
    checkpoint = deepcopy(exc.checkpoint or {})
    responses = dict(checkpoint.get("responses") or {})
    responses.pop(exc.phase, None)
    checkpoint["responses"] = responses
    checkpoint.pop("inflight_phase", None)
    trace = dict(checkpoint.get("pipeline_trace") or {})
    trace["phases"] = [
        item
        for item in trace.get("phases") or []
        if not isinstance(item, dict) or item.get("phase") != exc.phase
    ]
    checkpoint["pipeline_trace"] = trace
    return checkpoint


def _character_payload(item: Character) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "role_type": item.role_type,
        "age": item.age,
        "gender": item.gender,
        "identity": item.identity,
        "personality": item.personality,
        "appearance": item.appearance,
        "fixed_prompt": item.fixed_prompt,
        "asset_spec": deepcopy(item.asset_spec or {}),
        "status": item.status,
    }


def _scene_payload(item: Scene) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "description": item.description,
        "visual_style": item.visual_style,
        "atmosphere": item.atmosphere,
        "fixed_prompt": item.fixed_prompt,
        "asset_spec": deepcopy(item.asset_spec or {}),
        "status": item.status,
    }


def _prop_payload(item: Prop) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "description": item.description,
        "visual_prompt": item.visual_prompt,
        "story_function": item.story_function,
        "asset_spec": deepcopy(item.asset_spec or {}),
        "status": item.status,
    }


def _dialogue_payload(item: Dialogue) -> dict[str, Any]:
    return {
        "id": item.id,
        "character_id": item.character_id,
        "speaker": item.speaker_name,
        "speaker_name": item.speaker_name,
        "text": item.text,
        "translation_zh": item.translation_zh or "",
        "emotion": item.emotion or "",
        "sequence_order": item.sequence_order,
        "beat_id": item.beat_id or "",
        "sound_cues": list(item.sound_cues or []),
    }


def _asset_payload(item: Asset) -> dict[str, Any]:
    return {
        "id": item.id,
        "asset_type": item.asset_type,
        "asset_role": item.asset_role,
        "entity_type": item.entity_type,
        "entity_id": item.entity_id,
        "variant_key": item.variant_key,
        "version": item.version,
        "uri": item.uri,
        "mime_type": item.mime_type,
        "width": item.width,
        "height": item.height,
        "duration_sec": str(item.duration_sec) if isinstance(item.duration_sec, Decimal) else item.duration_sec,
        "provider": item.provider,
        "model": item.model,
        "status": item.status,
        "is_selected": item.is_selected,
    }
