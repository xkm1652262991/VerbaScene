from datetime import datetime, timezone
import json
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import build_entity_extraction_prompt, parse_entity_extraction_response
from app.agents.style import resolve_visual_style
from app.agents.entity_autofill import autofill_entity_asset_specs
from app.agents.entity_design import (
    compile_character_asset_fields,
    compile_prop_asset_fields,
    compile_scene_asset_fields,
    normalize_character_asset_spec,
    normalize_prop_asset_spec,
    normalize_scene_asset_spec,
)
from app.core.config import settings
from app.models import Character, GenerationTask, Project, Prop, Scene, Script
from app.providers.defaults import provider_registry
from app.providers.types import ProviderRequest, ProviderStatus, ProviderType
from app.schemas.entity import CharacterUpdate, PropUpdate, SceneUpdate
from app.scripts.speaker_policy import filter_visual_characters
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


def get_latest_script_for_entities(db: Session, project_id: str) -> Script:
    script = db.scalar(
        select(Script)
        .where(Script.project_id == project_id)
        .order_by(Script.version.desc(), Script.created_at.desc()),
    )
    if script is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Script not found")
    return script


def get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def list_characters(db: Session, project_id: str) -> list[Character]:
    return _list_current_characters(db, project_id)


def list_scenes(db: Session, project_id: str) -> list[Scene]:
    return _list_current_entities(db, project_id, Scene)


def list_props(db: Session, project_id: str) -> list[Prop]:
    return _list_current_entities(db, project_id, Prop)


def list_characters_page(
    db: Session,
    project_id: str,
    *,
    offset: int = 0,
    limit: int = 500,
) -> tuple[list[Character], int]:
    items = list_characters(db, project_id)
    return items[offset : offset + limit], len(items)


def list_scenes_page(
    db: Session,
    project_id: str,
    *,
    offset: int = 0,
    limit: int = 500,
) -> tuple[list[Scene], int]:
    items = list_scenes(db, project_id)
    return items[offset : offset + limit], len(items)


def list_props_page(
    db: Session,
    project_id: str,
    *,
    offset: int = 0,
    limit: int = 500,
) -> tuple[list[Prop], int]:
    items = list_props(db, project_id)
    return items[offset : offset + limit], len(items)


def list_all_entities(db: Session, project_id: str) -> tuple[list[Character], list[Scene], list[Prop]]:
    return (
        list(db.scalars(select(Character).where(Character.project_id == project_id).order_by(Character.created_at)).all()),
        list(db.scalars(select(Scene).where(Scene.project_id == project_id).order_by(Scene.created_at)).all()),
        list(db.scalars(select(Prop).where(Prop.project_id == project_id).order_by(Prop.created_at)).all()),
    )


def get_current_entities(db: Session, project_id: str) -> tuple[list[Character], list[Scene], list[Prop]]:
    return (
        _list_current_characters(db, project_id),
        _list_current_entities(db, project_id, Scene),
        _list_current_entities(db, project_id, Prop),
    )


def get_current_approved_entities(db: Session, project_id: str) -> tuple[list[Character], list[Scene], list[Prop]]:
    """Compatibility alias: version adoption replaced stage approval."""
    return get_current_entities(db, project_id)


def _list_current_characters(db: Session, project_id: str) -> list[Character]:
    return filter_visual_characters(
        _list_current_entities(db, project_id, Character),
        _latest_script_scenes(db, project_id),
    )


def _latest_script_scenes(db: Session, project_id: str) -> list[dict[str, Any]]:
    scenes = db.scalar(
        select(Script.scenes)
        .where(Script.project_id == project_id)
        .order_by(Script.version.desc(), Script.created_at.desc())
        .limit(1)
    )
    return scenes if isinstance(scenes, list) else []


def _list_current_entities(
    db: Session,
    project_id: str,
    model: type[Character] | type[Scene] | type[Prop],
    *,
    status_filter: str | None = None,
):
    latest_task = db.scalar(
        select(GenerationTask)
        .where(GenerationTask.project_id == project_id)
        .where(GenerationTask.task_type == "entity_extraction")
        .where(GenerationTask.status == ProviderStatus.SUCCEEDED.value)
        .order_by(GenerationTask.started_at.desc(), GenerationTask.created_at.desc())
    )
    statement = select(model).where(model.project_id == project_id)
    if status_filter is not None:
        statement = statement.where(model.status == status_filter)
    if latest_task and latest_task.started_at:
        statement = statement.where(model.created_at >= latest_task.started_at)
    items = list(db.scalars(statement.order_by(model.created_at)).all())
    if items:
        return _latest_entities_by_name(items)
    if latest_task and latest_task.started_at:
        return []

    fallback_statement = select(model).where(model.project_id == project_id)
    if status_filter is not None:
        fallback_statement = fallback_statement.where(model.status == status_filter)
    fallback_items = list(
        db.scalars(
            fallback_statement.order_by(model.created_at),
        ).all()
    )
    return _latest_entities_by_name(fallback_items)


def _latest_entities_by_name(items):
    by_name = {}
    for item in items:
        key = "".join((item.name or item.id).strip().lower().split()) or item.id
        current = by_name.get(key)
        if current is None or (item.created_at, item.updated_at, item.id) >= (current.created_at, current.updated_at, current.id):
            by_name[key] = item
    return sorted(by_name.values(), key=lambda item: item.created_at)


def generate_entities(db: Session, project_id: str) -> tuple[list[Character], list[Scene], list[Prop], GenerationTask]:
    project = get_project_or_404(db, project_id)
    script = get_latest_script_for_entities(db, project_id)
    previous_characters, previous_scenes, previous_props = get_current_entities(db, project_id)
    approved_entity_context = _serialize_entity_context(
        previous_characters,
        previous_scenes,
        previous_props,
    )
    agent_config = resolve_agent_config(
        db,
        agent_type="extractor",
        default_provider=settings.llm_provider,
        default_model=settings.llm_model,
    )
    provider = provider_registry.get(ProviderType.LLM, agent_config.provider)
    model = agent_config.model or provider.model

    task = GenerationTask(
        project_id=project_id,
        task_type="entity_extraction",
        provider=provider.name,
        model=model,
        input_payload={
            "script_id": script.id,
            "script_content": script.content,
            "script_scenes": script.scenes if isinstance(script.scenes, list) else [],
            "previous_approved_entities": approved_entity_context,
            "project_style": project.style,
        },
        status=ProviderStatus.RUNNING.value,
        started_at=datetime.now(timezone.utc),
    )
    db.add(task)
    db.flush()
    mark_stage_running(db, project_id, "entities", task_id=task.id)
    mark_task_running(db, task, "文本模型正在提取资产设定", 15)

    provider_response = provider.submit(
        ProviderRequest(
            project_id=project_id,
            task_id=task.id,
            model=model,
            prompt=_with_agent_prompt(
                agent_config.system_prompt,
                _with_approved_entity_context(
                    build_entity_extraction_prompt(
                        project,
                        script,
                        style_sentence=resolve_visual_style(project.style, project.creative_settings),
                    ),
                    approved_entities=approved_entity_context,
                ),
            ),
            params={"temperature": 0.35},
            metadata={"stage": "entity_extraction"},
        ),
    )

    task.provider_task_id = provider_response.provider_task_id
    if provider_response.status != ProviderStatus.SUCCEEDED:
        message = (
            provider_response.error.error_message
            if provider_response.error is not None
            else "LLM provider failed during entity extraction"
        )
        code = (
            provider_response.error.error_code
            if provider_response.error is not None
            else "llm_provider_failed"
        )
        mark_task_failed(
            db,
            task,
            code=code,
            message=message,
            raw_response=provider_response.raw_response,
            provider_task_id=provider_response.provider_task_id,
            failure_reason=normalize_failure_reason(
                code=code,
                message=message,
                raw_response=provider_response.raw_response,
                stage="entities",
                task_type=task.task_type,
                provider=provider.name,
                retryable=provider_response.error.is_retryable if provider_response.error else True,
            ),
        )
        mark_stage_failed(db, project_id, "entities", summary=message, error_code=code)
        db.add(task)
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=message)

    try:
        update_task_progress(db, task, "正在解析资产设定", 70)
        result = parse_entity_extraction_response(
            str(provider_response.raw_response.get("text", "")),
            style_sentence=resolve_visual_style(project.style, project.creative_settings),
        )
        result = autofill_entity_asset_specs(
            result,
            script_scenes=script.scenes,
            script_content=script.content,
        )
        result["characters"] = filter_visual_characters(result["characters"], script.scenes)
        update_task_progress(db, task, "已根据剧本和资产类型自动补全设定", 78)
    except (ValueError, json.JSONDecodeError) as exc:
        message = f"Entity extraction JSON parse failed: {exc}"
        mark_task_failed(
            db,
            task,
            code="entity_extraction_parse_failed",
            message=message,
            raw_response=provider_response.raw_response,
            provider_task_id=provider_response.provider_task_id,
            failure_reason=normalize_failure_reason(
                code="entity_extraction_parse_failed",
                message=message,
                raw_response=provider_response.raw_response,
                stage="entities",
                task_type=task.task_type,
                provider=provider.name,
            ),
        )
        mark_stage_failed(db, project_id, "entities", summary=message, error_code="entity_extraction_parse_failed")
        db.add(task)
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=message) from exc

    quality_report = _build_entity_quality_report(result)
    return _persist_entity_generation_result(
        db,
        project_id=project_id,
        task=task,
        result=result,
        quality_report=quality_report,
        provider_raw_response=provider_response.raw_response,
        provider_task_id=provider_response.provider_task_id,
        generation_context={
            "previous_approved_entities": approved_entity_context,
        },
        stage_action="生成",
    )


def recover_entity_extraction_task(
    db: Session,
    source_task_id: str,
) -> tuple[list[Character], list[Scene], list[Prop], GenerationTask]:
    """Replay a saved failed extraction response without calling its provider again."""
    source_task = db.get(GenerationTask, source_task_id)
    if source_task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if source_task.task_type != "entity_extraction" or source_task.status != ProviderStatus.FAILED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a failed entity extraction task can be recovered",
        )

    project_id = source_task.project_id
    get_project_or_404(db, project_id)
    script = get_latest_script_for_entities(db, project_id)
    source_input = source_task.input_payload if isinstance(source_task.input_payload, dict) else {}
    if source_input.get("script_id") != script.id or source_input.get("script_content") != script.content:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The failed task does not match the current approved script",
        )

    succeeded_tasks = db.scalars(
        select(GenerationTask)
        .where(GenerationTask.project_id == project_id)
        .where(GenerationTask.task_type == "entity_extraction")
        .where(GenerationTask.status == ProviderStatus.SUCCEEDED.value)
        .order_by(GenerationTask.created_at.desc())
    ).all()
    if any(
        isinstance(item.input_payload, dict)
        and item.input_payload.get("recovered_from_task_id") == source_task_id
        for item in succeeded_tasks
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This failed task has already been recovered",
        )

    source_raw_response = source_task.raw_response if isinstance(source_task.raw_response, dict) else {}
    provider_raw_response = source_raw_response.get("provider")
    if not isinstance(provider_raw_response, dict):
        provider_raw_response = source_raw_response
    response_text = provider_raw_response.get("text")
    if not isinstance(response_text, str) or not response_text.strip():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The failed task has no saved provider text to recover",
        )

    try:
        result = parse_entity_extraction_response(response_text)
        result = autofill_entity_asset_specs(
            result,
            script_scenes=script.scenes,
            script_content=script.content,
        )
        result["characters"] = filter_visual_characters(result["characters"], script.scenes)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Saved entity extraction response cannot be recovered: {exc}",
        ) from exc

    quality_report = _build_entity_quality_report(result)
    approved_entity_context = source_input.get("previous_approved_entities")
    if not isinstance(approved_entity_context, dict):
        approved_entity_context = {}
    task = GenerationTask(
        project_id=project_id,
        task_type="entity_extraction",
        provider="local_recovery",
        model=source_task.model,
        provider_task_id=source_task.provider_task_id,
        input_payload={
            **source_input,
            "recovered_from_task_id": source_task.id,
            "source_provider": source_task.provider,
            "recovery_mode": "saved_response_replay",
        },
        status=ProviderStatus.RUNNING.value,
        progress=90,
        progress_label="正在恢复已保存的资产设定",
        started_at=datetime.now(timezone.utc),
    )
    db.add(task)
    db.flush()
    mark_stage_running(db, project_id, "entities", task_id=task.id)

    return _persist_entity_generation_result(
        db,
        project_id=project_id,
        task=task,
        result=result,
        quality_report=quality_report,
        provider_raw_response=provider_raw_response,
        provider_task_id=source_task.provider_task_id,
        generation_context={
            "previous_approved_entities": approved_entity_context,
        },
        stage_action="从已保存结果恢复",
        recovery_metadata={
            "mode": "saved_response_replay",
            "source_task_id": source_task.id,
            "source_provider": source_task.provider,
            "provider_called": False,
        },
    )


def _persist_entity_generation_result(
    db: Session,
    *,
    project_id: str,
    task: GenerationTask,
    result: dict[str, list[Any]],
    quality_report: dict[str, list[dict[str, str]]],
    provider_raw_response: dict[str, Any],
    provider_task_id: str | None,
    generation_context: dict[str, Any],
    stage_action: str,
    recovery_metadata: dict[str, Any] | None = None,
) -> tuple[list[Character], list[Scene], list[Prop], GenerationTask]:
    characters = [
        Character(
            project_id=project_id,
            status="ready_for_review",
            **item,
        )
        for item in result["characters"]
    ]
    scenes = [
        Scene(
            project_id=project_id,
            status="ready_for_review",
            **item,
        )
        for item in result["scenes"]
    ]
    props = [
        Prop(
            project_id=project_id,
            status="ready_for_review",
            **item,
        )
        for item in result["props"]
    ]

    db.add_all([*characters, *scenes, *props])
    db.flush()
    latest_script = get_latest_script_for_entities(db, project_id)
    synchronize_script_dialogues(db, latest_script)
    task_raw_response: dict[str, Any] = {
        "provider": provider_raw_response,
        "agent_result": result,
        "quality_report": quality_report,
        "generation_context": generation_context,
    }
    if recovery_metadata:
        task_raw_response["recovery"] = recovery_metadata
    task.raw_response = task_raw_response
    mark_task_succeeded(
        db,
        task,
        output_asset_ids=[],
        result_payload={
            "character_count": len(characters),
            "scene_count": len(scenes),
            "prop_count": len(props),
            "quality_report": quality_report,
        },
        provider_task_id=provider_task_id,
    )
    mark_stage_ready(
        db,
        project_id,
        "entities",
        task_id=task.id,
        summary=f"角色、场景和道具已{stage_action}，可继续编辑或生成资产",
        metadata={
            "character_count": len(characters),
            "scene_count": len(scenes),
            "prop_count": len(props),
            "quality_blocker_count": len(quality_report["blockers"]),
            "quality_warning_count": len(quality_report["warnings"]),
            **({"recovery": recovery_metadata} if recovery_metadata else {}),
        },
    )
    mark_downstream_stages_pending(db, project_id, "entities", summary="资产设定已更新，需要重新生成分镜和后续内容")
    db.add(task)
    db.commit()

    for item in [*characters, *scenes, *props, task]:
        db.refresh(item)
    return characters, scenes, props, task


def _serialize_entity_context(
    characters: list[Character],
    scenes: list[Scene],
    props: list[Prop],
) -> dict[str, list[dict[str, Any]]]:
    return {
        "characters": [
            _entity_fields(
                item,
                (
                    "id",
                    "name",
                    "role_type",
                    "age",
                    "gender",
                    "identity",
                    "personality",
                    "appearance",
                    "fixed_prompt",
                    "asset_spec",
                ),
            )
            for item in characters
        ],
        "scenes": [
            _entity_fields(
                item,
                ("id", "name", "description", "visual_style", "atmosphere", "fixed_prompt", "asset_spec"),
            )
            for item in scenes
        ],
        "props": [
            _entity_fields(
                item,
                ("id", "name", "description", "visual_prompt", "story_function", "asset_spec"),
            )
            for item in props
        ],
    }


def _with_approved_entity_context(
    prompt: str,
    *,
    approved_entities: dict[str, list[dict[str, Any]]],
) -> str:
    return (
        f"{prompt}\n\n"
        "已确认资产上下文：\n"
        "- previous_approved_entities 是上一版已确认资产。同一实体再次出现时，应继承其不可变身份特征，除非已确认剧本明确要求改变。\n"
        "- 有自主动作、表情或剧情行为的人、动物、怪物和自主机器人必须归入 characters，不得归入 props。\n"
        "- 旁白、画外音、解说、系统语音和未出镜的虚拟引导声不是可视角色；即使旧资产中存在同名记录，也不得继承或生成角色参考图。\n"
        "- 受伤、康复、湿透、表情、姿态、正在手持某物等是镜头状态，不得写进实体的永久身份或固定视觉提示词。\n"
        f"已确认资产 JSON：\n{json.dumps(approved_entities, ensure_ascii=False, default=str)}"
    )


def _build_entity_quality_report(result: dict[str, list[Any]]) -> dict[str, list[dict[str, str]]]:
    return {"blockers": [], "warnings": []}


def _entity_fields(item: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: _raw_entity_value(item, field) for field in fields if _raw_entity_value(item, field) is not None}


def _raw_entity_value(item: Any, field: str) -> Any:
    if isinstance(item, dict):
        return item.get(field)
    return getattr(item, field, None)


def _with_agent_prompt(system_prompt: str | None, prompt: str) -> str:
    if not system_prompt:
        return prompt
    return f"{system_prompt.strip()}\n\n任务输入：\n{prompt}"


def update_character(db: Session, character_id: str, payload: CharacterUpdate) -> Character:
    character = db.get(Character, character_id)
    if character is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Character not found")
    updates = payload.model_dump(exclude_unset=True)
    asset_spec = normalize_character_asset_spec(
        updates.pop("asset_spec", character.asset_spec),
        legacy_appearance=character.appearance,
    )
    for field, value in updates.items():
        setattr(character, field, value)
    character.asset_spec = asset_spec
    character.appearance, character.fixed_prompt = compile_character_asset_fields(
        name=character.name,
        identity=character.identity,
        age=character.age,
        gender=character.gender,
        asset_spec=asset_spec,
    )
    main_prompt = (asset_spec.get("image_prompts") or {}).get("character_main_ref", {})
    if isinstance(main_prompt, dict) and isinstance(main_prompt.get("positive_prompt"), str):
        character.fixed_prompt = main_prompt["positive_prompt"].strip()
    character.status = "ready_for_review"
    db.add(character)
    mark_stage_ready(
        db,
        character.project_id,
        "entities",
        summary=f"角色「{character.name}」已保存",
        metadata={"entity_type": "character", "entity_id": character.id, "manual_update": True},
    )
    mark_downstream_stages_pending(db, character.project_id, "entities", summary="资产设定已手动更新，需要重新生成分镜和后续内容")
    db.commit()
    db.refresh(character)
    return character


def update_scene(db: Session, scene_id: str, payload: SceneUpdate) -> Scene:
    scene = db.get(Scene, scene_id)
    if scene is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scene not found")
    updates = payload.model_dump(exclude_unset=True)
    asset_spec = normalize_scene_asset_spec(
        updates.pop("asset_spec", scene.asset_spec),
        legacy_description=scene.description,
    )
    for field, value in updates.items():
        setattr(scene, field, value)
    scene.asset_spec = asset_spec
    scene.description, scene.visual_style, scene.atmosphere, scene.fixed_prompt = compile_scene_asset_fields(
        name=scene.name,
        asset_spec=asset_spec,
    )
    scene_prompt = (asset_spec.get("image_prompts") or {}).get("scene_ref", {})
    if isinstance(scene_prompt, dict) and isinstance(scene_prompt.get("positive_prompt"), str):
        scene.fixed_prompt = scene_prompt["positive_prompt"].strip()
    scene.status = "ready_for_review"
    db.add(scene)
    mark_stage_ready(
        db,
        scene.project_id,
        "entities",
        summary=f"场景「{scene.name}」已保存",
        metadata={"entity_type": "scene", "entity_id": scene.id, "manual_update": True},
    )
    mark_downstream_stages_pending(db, scene.project_id, "entities", summary="资产设定已手动更新，需要重新生成分镜和后续内容")
    db.commit()
    db.refresh(scene)
    return scene


def update_prop(db: Session, prop_id: str, payload: PropUpdate) -> Prop:
    prop = db.get(Prop, prop_id)
    if prop is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prop not found")
    updates = payload.model_dump(exclude_unset=True)
    asset_spec = normalize_prop_asset_spec(
        updates.pop("asset_spec", prop.asset_spec),
        legacy_description=prop.description,
        legacy_story_function=prop.story_function,
    )
    for field, value in updates.items():
        setattr(prop, field, value)
    prop.asset_spec = asset_spec
    prop.description, prop.visual_prompt, prop.story_function = compile_prop_asset_fields(
        name=prop.name,
        asset_spec=asset_spec,
    )
    prop_prompt = (asset_spec.get("image_prompts") or {}).get("prop_ref", {})
    if isinstance(prop_prompt, dict) and isinstance(prop_prompt.get("positive_prompt"), str):
        prop.visual_prompt = prop_prompt["positive_prompt"].strip()
    prop.status = "ready_for_review"
    db.add(prop)
    mark_stage_ready(
        db,
        prop.project_id,
        "entities",
        summary=f"道具「{prop.name}」已保存",
        metadata={"entity_type": "prop", "entity_id": prop.id, "manual_update": True},
    )
    mark_downstream_stages_pending(db, prop.project_id, "entities", summary="资产设定已手动更新，需要重新生成分镜和后续内容")
    db.commit()
    db.refresh(prop)
    return prop
