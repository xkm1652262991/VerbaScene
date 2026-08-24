from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.agents.script_contracts import SCRIPT_QUALITY_PIPELINE_VERSION, ScriptGenerationInput
from app.agents.script_pipeline import ScriptPipelineFailure, run_script_pipeline
from app.agents.script_prompts import script_pipeline_system_prompt
from app.agents.script_screenplay import (
    extract_dialogues_from_scenes,
    normalize_production_scenes,
    render_readable_script,
)
from app.core.config import settings
from app.models import Chapter, GenerationTask, Project, Script
from app.providers.defaults import provider_registry
from app.providers.types import ProviderType
from app.schemas.script import ScriptUpdate
from app.services.agent_config_service import resolve_agent_config
from app.services.dialogue_service import synchronize_script_dialogues
from app.services.failure_reason_service import normalize_failure_reason
from app.services.task_service import mark_task_failed, mark_task_running, mark_task_succeeded, update_task_progress
from app.services.workflow_state_service import (
    mark_downstream_stages_pending,
    mark_stage_failed,
    mark_stage_ready,
    mark_stage_running,
)


SCRIPT_GENERATION_TASK_TYPE = "script_generation"


def get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.scalar(
        select(Project)
        .options(selectinload(Project.chapters))
        .where(Project.id == project_id),
    )
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def get_primary_chapter_or_404(project: Project) -> Chapter:
    if not project.chapters:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project chapter not found")
    return project.chapters[0]


def get_latest_script(db: Session, project_id: str) -> Script | None:
    return db.scalar(
        select(Script)
        .where(Script.project_id == project_id)
        .order_by(Script.version.desc(), Script.created_at.desc()),
    )


def get_script_or_404(db: Session, script_id: str) -> Script:
    script = db.get(Script, script_id)
    if script is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Script not found")
    return script


def create_script_generation_task(db: Session, project_id: str) -> GenerationTask:
    project = get_project_or_404(db, project_id)
    chapter = get_primary_chapter_or_404(project)
    active = db.scalar(
        select(GenerationTask)
        .where(GenerationTask.project_id == project_id)
        .where(GenerationTask.task_type == SCRIPT_GENERATION_TASK_TYPE)
        .where(GenerationTask.status.in_(("queued", "running")))
        .order_by(GenerationTask.created_at.desc())
    )
    if active is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"该项目已有剧本任务正在执行：{active.id}",
        )

    agent_config = resolve_agent_config(
        db,
        agent_type="script_rewriter",
        default_provider=settings.llm_provider,
        default_model=settings.llm_model,
    )
    provider = provider_registry.get(ProviderType.LLM, agent_config.provider)
    model = agent_config.model or provider.model
    review_config = resolve_agent_config(
        db,
        agent_type="script_reviewer",
        default_provider=provider.name,
        default_model=model,
    )
    review_provider = provider_registry.get(ProviderType.LLM, review_config.provider)
    review_model = review_config.model or review_provider.model
    temperatures = _pipeline_temperatures(agent_config)
    temperatures["script_review"] = _phase_temperature(review_config, "script_review", 0.2)
    source = _script_generation_input_from_records(project, chapter)
    task = GenerationTask(
        project_id=project_id,
        task_type=SCRIPT_GENERATION_TASK_TYPE,
        provider=provider.name,
        model=model,
        input_payload={
            "pipeline_version": SCRIPT_QUALITY_PIPELINE_VERSION,
            "script_input": source.to_payload(),
            "system_prompt": script_pipeline_system_prompt(agent_config.system_prompt),
            "review_runtime": {
                "provider": review_provider.name,
                "model": review_model,
                "system_prompt": script_pipeline_system_prompt(review_config.system_prompt),
            },
            "temperatures": temperatures,
        },
        status="queued",
        progress=0,
        progress_label="等待剧本生成",
        raw_response={
            "checkpoint": {
                "pipeline_version": SCRIPT_QUALITY_PIPELINE_VERSION,
                "responses": {},
                "pipeline_trace": {"phases": [], "fallbacks": []},
            }
        },
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def execute_script_generation_task(db: Session, task_id: str) -> tuple[Script, GenerationTask]:
    task = db.get(GenerationTask, task_id)
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.task_type != SCRIPT_GENERATION_TASK_TYPE:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该任务不属于剧本生成队列")
    if task.status == "succeeded":
        script_id = str((task.result_payload or {}).get("script_id") or "")
        return get_script_or_404(db, script_id), task
    if task.status not in {"queued", "running"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该剧本任务不可执行")

    source = ScriptGenerationInput.from_payload(task.input_payload)
    project = get_project_or_404(db, source.project_id)
    chapter = db.get(Chapter, source.chapter_id)
    if chapter is None or chapter.project_id != project.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="剧本任务的输入章节已不存在")

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
    mark_stage_running(db, project.id, "script", task_id=task.id)
    mark_task_running(db, task, "正在恢复或启动剧本流水线", max(5, task.progress))

    raw_response = task.raw_response if isinstance(task.raw_response, dict) else {}
    checkpoint = raw_response.get("checkpoint") if isinstance(raw_response.get("checkpoint"), dict) else {}

    def persist_checkpoint(value: dict, label: str, progress: int) -> None:
        task.raw_response = {"checkpoint": value}
        task.provider_task_id = _last_provider_task_id(value)
        update_task_progress(db, task, label, progress)

    try:
        pipeline_result = run_script_pipeline(
            provider=provider,
            review_provider=review_provider,
            source=source,
            task_id=task.id,
            model=model,
            review_model=review_model,
            system_prompt=str(task.input_payload.get("system_prompt") or ""),
            review_system_prompt=str(
                review_runtime.get("system_prompt") or task.input_payload.get("system_prompt") or ""
            ),
            temperatures=_task_temperatures(task.input_payload),
            checkpoint=checkpoint,
            on_checkpoint=persist_checkpoint,
        )
    except ScriptPipelineFailure as exc:
        failure_raw_response = {"checkpoint": exc.checkpoint}
        mark_task_failed(
            db,
            task,
            code=exc.code,
            message=exc.message,
            raw_response=failure_raw_response,
            provider_task_id=exc.provider_task_id,
            failure_reason=normalize_failure_reason(
                code=exc.code,
                message=exc.message,
                raw_response=failure_raw_response,
                stage="script",
                task_type=task.task_type,
                provider=task.provider,
                retryable=False,
            ),
        )
        mark_stage_failed(db, project.id, "script", summary=exc.message, error_code=exc.code)
        db.add(task)
        db.commit()
        raise

    next_version = (
        db.scalar(select(func.coalesce(func.max(Script.version), 0)).where(Script.project_id == project.id))
        or 0
    ) + 1
    final = pipeline_result.final
    script = Script(
        project_id=project.id,
        chapter_id=source.chapter_id,
        version=next_version,
        content=final["content"],
        scenes=final["scenes"],
        dialogues=final["dialogues"],
        status="ready_for_review",
    )
    db.add(script)
    db.flush()
    synchronize_script_dialogues(db, script)

    task.raw_response = {"checkpoint": pipeline_result.checkpoint}
    mark_task_succeeded(
        db,
        task,
        output_asset_ids=[],
        result_payload={
            "script_id": script.id,
            "version": next_version,
            "pipeline_version": SCRIPT_QUALITY_PIPELINE_VERSION,
            "quality_gate": pipeline_result.quality_gate,
            "issue_counts": pipeline_result.issue_counts,
            "patch_applied": pipeline_result.patch_applied,
            "patched_scene_nos": pipeline_result.patched_scene_nos,
            "resolved_issue_codes": pipeline_result.resolved_issue_codes,
            "unresolved_issue_codes": pipeline_result.unresolved_issue_codes,
            "contract_error_codes": [
                str(error.get("code"))
                for error in pipeline_result.contract_report.get("errors") or []
                if isinstance(error, dict) and error.get("code")
            ],
            "review_provider": review_provider.name,
            "review_model": review_model,
        },
        raw_response=task.raw_response,
        provider_task_id=_last_provider_task_id(pipeline_result.checkpoint),
    )
    mark_stage_ready(
        db,
        project.id,
        "script",
        task_id=task.id,
        summary=(
            f"剧本版本 {next_version} 已生成，可继续编辑"
            if pipeline_result.quality_gate == "pass"
            else f"剧本版本 {next_version} 已生成，建议先检查协作报告"
        ),
        metadata={
            "script_id": script.id,
            "version": next_version,
            "quality_gate": pipeline_result.quality_gate,
        },
    )
    mark_downstream_stages_pending(
        db,
        project.id,
        "script",
        summary="剧本已更新，需要重新生成资产设定和后续内容",
    )
    project.status = "in_progress"
    db.add_all([project, task])
    db.commit()
    db.refresh(script)
    db.refresh(task)
    return script, task


def generate_script_now(db: Session, project_id: str) -> tuple[Script, GenerationTask]:
    """Synchronous helper for tests and maintenance commands, never used by the HTTP route."""

    task = create_script_generation_task(db, project_id)
    return execute_script_generation_task(db, task.id)


def update_script(db: Session, script_id: str, payload: ScriptUpdate) -> Script:
    script = get_script_or_404(db, script_id)
    _synchronize_script(script, payload.scenes)
    synchronize_script_dialogues(db, script)
    script.status = "ready_for_review"
    script.approved_at = None
    mark_stage_ready(
        db,
        script.project_id,
        "script",
        summary=f"剧本版本 {script.version} 已保存",
        metadata={"script_id": script.id, "version": script.version, "manual_update": True},
    )
    mark_downstream_stages_pending(
        db,
        script.project_id,
        "script",
        summary="剧本已手动更新，需要重新生成资产设定和后续内容",
    )
    db.add(script)
    db.commit()
    db.refresh(script)
    return script


def _synchronize_script(
    script: Script,
    scenes: object,
    *,
    legacy_content: str = "",
    legacy_dialogues: object = None,
) -> None:
    try:
        normalized_scenes = normalize_production_scenes(
            scenes,
            legacy_content=legacy_content,
            legacy_dialogues=legacy_dialogues,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    script.scenes = normalized_scenes
    script.content = render_readable_script(normalized_scenes)
    script.dialogues = extract_dialogues_from_scenes(normalized_scenes)


def _pipeline_temperatures(agent_config) -> dict[str, float]:
    return {
        "story_blueprint": _phase_temperature(agent_config, "story_blueprint", 0.75),
        "script_draft": _phase_temperature(agent_config, "script_draft", 0.65),
        "script_review": _phase_temperature(agent_config, "script_review", 0.2),
        "script_patch": _phase_temperature(agent_config, "script_patch", 0.35),
        "structure_recovery": _phase_temperature(agent_config, "structure_recovery", 0.1),
    }


def _script_generation_input_from_records(
    project: Project,
    chapter: Chapter,
) -> ScriptGenerationInput:
    """Translate persistence records at the application boundary.

    The creative pipeline accepts only a stable value object and therefore has
    no dependency on SQLAlchemy models or the eventual database design.
    """

    creative_settings = (
        dict(project.creative_settings)
        if isinstance(getattr(project, "creative_settings", None), dict)
        else {}
    )
    return ScriptGenerationInput(
        project_id=str(project.id),
        chapter_id=str(chapter.id),
        title=str(project.title or ""),
        style=str(project.style or ""),
        target_duration_sec=int(project.target_duration_sec or 90),
        aspect_ratio=str(project.aspect_ratio or "16:9"),
        resolution=str(project.resolution or "854x480"),
        creative_settings=creative_settings,
        input_mode=str(chapter.input_mode or "imported_script"),
        outline=str(chapter.outline or ""),
        source_text=str(chapter.source_text or ""),
    )


def _phase_temperature(agent_config, phase: str, fallback: float) -> float:
    settings_payload = agent_config.settings if isinstance(agent_config.settings, dict) else {}
    candidate = settings_payload.get(f"{phase}_temperature")
    if candidate is None and phase == "script_draft":
        candidate = agent_config.temperature
    try:
        return max(0.0, min(2.0, float(candidate)))
    except (TypeError, ValueError):
        return fallback


def _task_temperatures(payload: dict) -> dict[str, float]:
    value = payload.get("temperatures") if isinstance(payload.get("temperatures"), dict) else {}
    defaults = {
        "story_blueprint": 0.75,
        "script_draft": 0.65,
        "script_review": 0.2,
        "script_patch": 0.35,
        "structure_recovery": 0.1,
    }
    return {
        phase: float(value.get(phase, fallback))
        for phase, fallback in defaults.items()
    }


def _last_provider_task_id(checkpoint: dict) -> str | None:
    trace = checkpoint.get("pipeline_trace") if isinstance(checkpoint, dict) else None
    phases = trace.get("phases") if isinstance(trace, dict) else None
    if not isinstance(phases, list):
        return None
    for item in reversed(phases):
        if isinstance(item, dict) and item.get("provider_task_id"):
            return str(item["provider_task_id"])
    return None
