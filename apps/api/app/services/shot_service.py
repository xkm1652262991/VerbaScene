from datetime import datetime, timezone
import re
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.agents.camera_language import professional_movement, professional_shot_size
from app.models import Asset, Character, Dialogue, GenerationTask, Project, Prop, Scene, Script, Shot
from app.production.shot_prompt_compiler import (
    VIDEO_PROMPT_COMPILER_VERSION,
    apply_video_prompt_field,
    select_entities,
    select_scene,
    shot_prompt_fingerprint,
)
from app.schemas.shot import ShotCreate, ShotReorderRequest, ShotUpdate
from app.services.asset_resolver_service import resolve_generation_assets
from app.services.dialogue_service import bind_dialogues_to_shots, list_dialogues
from app.services.entity_service import get_current_entities
from app.services.workflow_state_service import (
    mark_downstream_stages_pending,
    mark_stage_ready,
)


def get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def get_latest_approved_script(db: Session, project_id: str) -> Script:
    script = db.scalar(
        select(Script)
        .where(Script.project_id == project_id)
        .order_by(Script.version.desc(), Script.created_at.desc())
    )
    if script is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Script not found")
    return script


def list_shots(db: Session, project_id: str) -> list[Shot]:
    return list(
        db.scalars(
            select(Shot)
            .where(Shot.project_id == project_id)
            .where(Shot.is_current.is_(True))
            .order_by(Shot.shot_no, Shot.created_at)
        ).all()
    )


def list_shots_page(db: Session, project_id: str, offset: int = 0, limit: int = 500) -> tuple[list[Shot], int]:
    get_project_or_404(db, project_id)
    total = db.scalar(
        select(func.count(Shot.id))
        .where(Shot.project_id == project_id)
        .where(Shot.is_current.is_(True))
    ) or 0
    shots = list(
        db.scalars(
            select(Shot)
            .where(Shot.project_id == project_id)
            .where(Shot.is_current.is_(True))
            .order_by(Shot.shot_no, Shot.created_at)
            .offset(offset)
            .limit(limit)
        ).all()
    )
    return shots, total


def get_approved_entities(db: Session, project_id: str) -> tuple[list[Character], list[Scene], list[Prop]]:
    characters, scenes, props = get_current_entities(db, project_id)
    if not characters or not scenes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="生成片段方案前，请先从剧本提取角色和场景设定",
        )
    return characters, scenes, props


def generate_shots(db: Session, project_id: str) -> tuple[list[Shot], GenerationTask]:
    """Synchronous compatibility helper for tests and maintenance scripts."""

    from app.production.shot_direction.service import generate_shots_now

    return generate_shots_now(db, project_id)

def create_shot(db: Session, project_id: str, payload: ShotCreate) -> Shot:
    project = get_project_or_404(db, project_id)
    script = get_latest_approved_script(db, project_id)
    characters, scenes, props = get_approved_entities(db, project_id)
    data = payload.model_dump()
    data["shot_card"] = build_fallback_shot_card(data)
    _sync_prompts_from_card(data)
    dialogue_ids = {str(item) for item in data.get("dialogue_ids") or []}
    selected_dialogues = [
        dialogue
        for dialogue in list_dialogues(db, project_id)
        if dialogue.id in dialogue_ids
    ]
    apply_video_prompt_field(
        project=project,
        data=data,
        characters=characters,
        scenes=scenes,
        props=props,
        dialogues=selected_dialogues,
    )
    shot = Shot(
        project_id=project_id,
        script_id=script.id,
        shot_batch_id=_current_shot_batch_id(db, project_id) or uuid4().hex,
        is_current=True,
        status="ready_for_review",
        **data,
    )
    db.add(shot)
    db.flush()
    bind_dialogues_to_shots(db, project_id, [shot])
    card = dict(shot.shot_card or {})
    card["prompt_fingerprint"] = shot_prompt_fingerprint(
        project=project,
        shot=shot,
        characters=characters,
        scene=select_scene(scenes, shot.scene_id),
        props=select_entities(props, shot.prop_ids),
        dialogues=selected_dialogues,
    )
    shot.shot_card = card
    _mark_shot_changed(db, shot, "视频片段方案已手动新增")
    db.commit()
    db.refresh(shot)
    return shot


def get_shot_or_404(db: Session, shot_id: str) -> Shot:
    shot = db.get(Shot, shot_id)
    if shot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shot not found")
    return shot


def update_shot(db: Session, shot_id: str, payload: ShotUpdate) -> Shot:
    shot = get_shot_or_404(db, shot_id)
    updates = payload.model_dump(exclude_unset=True)
    source_changed = bool(
        {
            "scene_id",
            "description",
            "camera_shot",
            "camera_movement",
            "duration_sec",
            "dialogue_ids",
            "character_ids",
            "prop_ids",
            "shot_card",
            "image_prompt",
            "negative_prompt",
        }
        & updates.keys()
    )
    for field, value in updates.items():
        setattr(shot, field, value)
    if "duration_sec" in updates:
        card = dict(shot.shot_card or {})
        segment_plan = (
            dict(card.get("segment_plan"))
            if isinstance(card.get("segment_plan"), dict)
            else {}
        )
        segment_plan["duration_mode"] = "fixed" if updates["duration_sec"] is not None else "provider_auto"
        segment_plan["actual_duration_sec"] = None
        card["segment_plan"] = segment_plan
        shot.shot_card = card
    if "shot_card" in updates:
        data = _shot_to_dict(shot)
        _sync_prompts_from_card(data)
        shot.image_prompt = data.get("image_prompt")
        shot.negative_prompt = data.get("negative_prompt")
        shot.dialogue_ids = list(
            dict.fromkeys(
                dialogue_id
                for beat in (shot.shot_card or {}).get("beats", [])
                if isinstance(beat, dict)
                for dialogue_id in beat.get("dialogue_ids", [])
                if isinstance(dialogue_id, str)
            )
        )
        bind_dialogues_to_shots(db, shot.project_id, [shot])
    if {"image_prompt", "negative_prompt"} & updates.keys():
        _sync_storyboard_prompt_to_card(shot)
    if source_changed:
        shot.status = "ready_for_review"
        _mark_shot_changed(db, shot, f"片段 {shot.shot_no} 已保存")
    if "video_prompt" in updates:
        card = dict(shot.shot_card or {})
        card["prompt_source"] = "manual"
        card["prompt_stale"] = False
        shot.shot_card = card
    db.add(shot)
    db.commit()
    db.refresh(shot)
    return shot


def update_shot_video_reference(db: Session, shot_id: str, asset_id: str | None) -> Shot:
    shot = get_shot_or_404(db, shot_id)
    if asset_id:
        asset = db.get(Asset, asset_id)
        if (
            asset is None
            or asset.project_id != shot.project_id
            or asset.asset_type != "image"
            or asset.status != "approved"
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="视频主参考图必须是当前项目中已确认的图片资产",
            )

    card = dict(shot.shot_card or {})
    if asset_id:
        card["video_reference_asset_id"] = asset_id
    else:
        card.pop("video_reference_asset_id", None)
    card["prompt_stale"] = True
    card["stale_reason"] = (
        "已明确启用片段首帧，需要重新检查视频 Prompt"
        if asset_id
        else "已取消片段首帧，视频将使用角色/场景引用和文本 Prompt"
    )
    shot.shot_card = card
    db.add(shot)
    db.commit()
    db.refresh(shot)
    return shot


def update_shot_reference_assets(
    db: Session,
    shot_id: str,
    asset_ids: list[str],
) -> Shot:
    shot = get_shot_or_404(db, shot_id)
    ordered_ids = list(dict.fromkeys(str(item).strip() for item in asset_ids if str(item).strip()))
    if len(ordered_ids) != len(asset_ids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Reference asset ids must be non-empty and unique",
        )

    assets: list[Asset] = []
    for asset_id in ordered_ids:
        asset = db.get(Asset, asset_id)
        if (
            asset is None
            or asset.project_id != shot.project_id
            or asset.asset_type != "image"
            or asset.entity_type not in {"character", "scene", "prop"}
            or not asset.entity_id
            or not (
                asset.status == "approved"
                or (asset.is_selected and asset.status == "ready_for_review")
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Reference asset is not an adopted project image version: {asset_id}",
            )
        assets.append(asset)

    scene_assets = [asset for asset in assets if asset.entity_type == "scene"]
    if len(scene_assets) > 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A shot can reference at most one scene asset",
        )

    shot.character_ids = list(
        dict.fromkeys(
            asset.entity_id
            for asset in assets
            if asset.entity_type == "character" and asset.entity_id
        )
    )
    shot.scene_id = scene_assets[0].entity_id if scene_assets else None
    shot.prop_ids = list(
        dict.fromkeys(
            asset.entity_id
            for asset in assets
            if asset.entity_type == "prop" and asset.entity_id
        )
    )
    card = dict(shot.shot_card or {})
    card["reference_asset_ids"] = ordered_ids
    shot.shot_card = card
    shot.status = "ready_for_review"
    _mark_shot_changed(db, shot, f"片段 {shot.shot_no} 的引用资产已更新")
    db.commit()
    db.refresh(shot)
    return shot


def _sync_storyboard_prompt_to_card(shot: Shot) -> None:
    card = dict(shot.shot_card or {})
    prompts = dict(card.get("image_prompts") or {})
    storyboard = dict(prompts.get("shot_storyboard") or {})
    storyboard["positive_prompt"] = (shot.image_prompt or "").strip()
    storyboard["negative_prompt"] = (shot.negative_prompt or "").strip()
    storyboard.setdefault("visible_character_ids", _bounded_ids(shot.character_ids, set(shot.character_ids or [])))
    storyboard.setdefault("visible_prop_ids", _bounded_ids(shot.prop_ids, set(shot.prop_ids or [])))
    prompts["shot_storyboard"] = storyboard
    card["image_prompts"] = prompts
    card["image_prompt_flow"] = "manual"
    shot.shot_card = card


def _mark_shot_changed(db: Session, shot: Shot, summary: str) -> None:
    card = dict(shot.shot_card or {})
    card["prompt_stale"] = True
    card["stale_reason"] = summary
    shot.shot_card = card
    db.add(shot)
    mark_stage_ready(
        db,
        shot.project_id,
        "shots",
        summary=summary,
        metadata={"shot_id": shot.id, "manual_update": True},
    )
    mark_downstream_stages_pending(
        db,
        shot.project_id,
        "videos",
        summary="片段内容已更新，需要检查视频 Prompt 和导出",
    )


def delete_shot(db: Session, shot_id: str) -> None:
    shot = get_shot_or_404(db, shot_id)
    shot.is_current = False
    shot.status = "rejected"
    db.add(shot)
    _mark_shot_changed(db, shot, "视频片段方案已手动删除")
    db.commit()


def reorder_shots(db: Session, payload: ShotReorderRequest) -> list[Shot]:
    project_id: str | None = None
    for item in payload.shots:
        shot = get_shot_or_404(db, item.id)
        shot.shot_no = item.shot_no
        project_id = shot.project_id
        db.add(shot)
    if project_id:
        mark_stage_ready(db, project_id, "shots", summary="视频片段顺序已更新")
        mark_downstream_stages_pending(db, project_id, "shots", summary="分镜顺序已更新，需要重新生成图片和后续内容")
    db.commit()
    return list_shots(db, project_id) if project_id else []


def _current_shot_batch_id(db: Session, project_id: str) -> str | None:
    shot = db.scalar(
        select(Shot)
        .where(Shot.project_id == project_id)
        .where(Shot.is_current.is_(True))
        .where(Shot.shot_batch_id.is_not(None))
        .order_by(Shot.updated_at.desc(), Shot.created_at.desc())
    )
    return shot.shot_batch_id if shot else None


def preview_shot_video_prompt(db: Session, shot_id: str) -> dict[str, Any]:
    shot = get_shot_or_404(db, shot_id)
    project = get_project_or_404(db, shot.project_id)
    characters, scenes, props = get_current_entities(db, shot.project_id)
    dialogue_ids = {str(item) for item in shot.dialogue_ids or []}
    dialogues = [
        dialogue
        for dialogue in list_dialogues(db, shot.project_id)
        if dialogue.id in dialogue_ids or dialogue.shot_id == shot.id
    ]
    reference_assets = _shot_prompt_reference_assets(db, shot)
    reference_variant_keys: dict[str, str] = {}
    reference_media_labels: dict[str, str] = {}
    storyboard_media_label: str | None = None
    for item in reference_assets:
        if item["reference_role"] == "video_first_frame":
            storyboard_media_label = item["media_label"]
        if item["entity_id"] and item["variant_key"]:
            reference_variant_keys.setdefault(item["entity_id"], item["variant_key"])
        if item["entity_id"] and item["media_label"]:
            reference_media_labels.setdefault(item["entity_id"], item["media_label"])
    data = _shot_to_dict(shot)
    apply_video_prompt_field(
        project=project,
        data=data,
        characters=characters,
        scenes=scenes,
        props=props,
        dialogues=dialogues,
        reference_variant_keys=reference_variant_keys,
        reference_media_labels=reference_media_labels,
        storyboard_media_label=storyboard_media_label,
    )
    prompt = str(data["video_prompt"])
    fingerprint = shot_prompt_fingerprint(
        project=project,
        shot=shot,
        characters=characters,
        scene=select_scene(scenes, shot.scene_id),
        props=select_entities(props, shot.prop_ids),
        dialogues=dialogues,
    )
    return {
        "shot_id": shot.id,
        "prompt": prompt,
        "input_fingerprint": fingerprint,
        "current_fingerprint": (shot.shot_card or {}).get("prompt_fingerprint"),
        "stale": bool((shot.shot_card or {}).get("prompt_stale"))
        or (shot.shot_card or {}).get("prompt_fingerprint") != fingerprint,
        "reference_tokens": list(
            dict.fromkeys(
                [
                    *(item["token"] for item in reference_assets),
                    *re.findall(r"@[^\s；，。,.\n]+", prompt),
                ]
            )
        ),
        "reference_assets": reference_assets,
    }


def compile_shot_video_prompt(db: Session, shot_id: str) -> Shot:
    shot = get_shot_or_404(db, shot_id)
    preview = preview_shot_video_prompt(db, shot_id)
    shot.video_prompt = preview["prompt"]
    card = dict(shot.shot_card or {})
    card["prompt_fingerprint"] = preview["input_fingerprint"]
    card["prompt_stale"] = False
    card["prompt_source"] = "compiled"
    card["prompt_compiler_version"] = VIDEO_PROMPT_COMPILER_VERSION
    card["prompt_compiled_at"] = datetime.now(timezone.utc).isoformat()
    card.pop("stale_reason", None)
    shot.shot_card = card
    db.add(shot)
    db.commit()
    db.refresh(shot)
    return shot


def _shot_prompt_reference_assets(db: Session, shot: Shot) -> list[dict[str, str]]:
    resolution = resolve_generation_assets(
        db,
        shot.project_id,
        stage="shot_video",
        shot=shot,
    )
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    media_counts = {"image": 0, "video": 0, "audio": 0}
    media_labels = {"image": "图片", "video": "视频", "audio": "音频"}
    for reference in sorted(resolution.reference_assets, key=lambda item: item.priority):
        media_type = (
            reference.asset_type
            if reference.asset_type in media_counts
            else "image"
        )
        media_counts[media_type] += 1
        media_label = f"{media_labels[media_type]}{media_counts[media_type]}"
        asset = db.get(Asset, reference.asset_id)
        if asset is None or asset.id in seen:
            continue
        entity_type = reference.entity_type or "shot"
        entity_id = reference.entity_id or shot.id
        entity = {
            "character": db.get(Character, entity_id),
            "scene": db.get(Scene, entity_id),
            "prop": db.get(Prop, entity_id),
            "shot": shot if entity_id == shot.id else None,
        }.get(entity_type)
        if entity is None:
            continue
        seen.add(asset.id)
        prefix = {
            "character": "角色",
            "scene": "场景",
            "prop": "道具",
            "shot": "片段",
        }[entity_type]
        variant_key = asset.variant_key or "base"
        name = (
            "首帧"
            if entity_type == "shot"
            else str(entity.name).strip().replace(" ", "_")
        )
        result.append(
            {
                "asset_id": asset.id,
                "token": f"@{prefix}_{name}_{variant_key}",
                "entity_type": entity_type,
                "entity_id": entity_id,
                "variant_key": variant_key,
                "reference_role": reference.reference_role,
                "media_label": media_label,
                "uri": asset.uri,
            }
        )
    return result


def build_fallback_shot_card(data: dict[str, Any]) -> dict[str, Any]:
    card = dict(data.get("shot_card") or {})
    description = str(data.get("description") or "").strip()
    card.setdefault("story_purpose", description or "推进剧情")
    card.setdefault("emotional_intent", "根据剧情建立明确情绪")
    card.setdefault(
        "camera",
        {
            "shot_size": professional_shot_size(data.get("camera_shot") or ""),
            "angle": "",
            "movement": professional_movement(data.get("camera_movement") or ""),
        },
    )
    card.setdefault(
        "action",
        {"start_state": description, "main_action": description, "end_state": description},
    )
    card.setdefault(
        "frame_plan",
        {"first_frame": description, "key_frame": description, "last_frame": description},
    )
    card.setdefault(
        "storyboard_frame",
        {
            "description": description,
            "narrative_focus": description,
            "character_ids": list(data.get("character_ids") or []),
            "prop_ids": list(data.get("prop_ids") or []),
        },
    )
    card.setdefault("schema_version", 3)
    card.setdefault(
        "segment_plan",
        {
            "duration_mode": "provider_auto",
            "expected_duration_range": {"min_sec": 4, "max_sec": 15},
            "actual_duration_sec": None,
            "internal_shot_count": len(card.get("beats") or []) or 1,
        },
    )
    card.setdefault(
        "beats",
        [
            {
                "beat_id": "beat-1",
                "camera": "，".join(
                    item
                    for item in (
                        str(data.get("camera_shot") or "").strip(),
                        str(data.get("camera_movement") or "").strip(),
                    )
                    if item
                ),
                "action": description,
                "dialogue_ids": list(data.get("dialogue_ids") or []),
                "sound_cues": [],
            }
        ],
    )
    timing = dict(card.get("motion_timing") or {})
    timing["beats"] = card["beats"]
    card["motion_timing"] = timing
    card.setdefault("risk_flags", [])
    card.setdefault("review_checklist", ["角色一致", "场景正确", "动作合理", "无水印乱码"])
    if isinstance(data.get("image_prompt"), str) and data["image_prompt"].strip():
        prompts = dict(card.get("image_prompts") or {})
        prompts.setdefault(
            "shot_storyboard",
            {
                "positive_prompt": data["image_prompt"].strip(),
                "negative_prompt": str(data.get("negative_prompt") or "").strip(),
                "visible_character_ids": list(data.get("character_ids") or []),
                "visible_prop_ids": list(data.get("prop_ids") or []),
            },
        )
        card["image_prompts"] = prompts
    return card


def _sync_prompts_from_card(data: dict[str, Any]) -> bool:
    card = data.get("shot_card")
    prompts = card.get("image_prompts") if isinstance(card, dict) else None
    storyboard = prompts.get("shot_storyboard") if isinstance(prompts, dict) else None
    if not isinstance(storyboard, dict):
        return False
    positive = storyboard.get("positive_prompt")
    if not isinstance(positive, str) or not positive.strip():
        return False
    data["image_prompt"] = positive.strip()
    negative = storyboard.get("negative_prompt")
    data["negative_prompt"] = negative.strip() if isinstance(negative, str) else None
    return True


def _bounded_ids(value: Any, allowed: set[str]) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item in allowed))


def _shot_to_dict(shot: Shot) -> dict[str, Any]:
    return {
        "scene_id": shot.scene_id,
        "description": shot.description,
        "camera_shot": shot.camera_shot,
        "camera_movement": shot.camera_movement,
        "duration_sec": shot.duration_sec,
        "dialogue_ids": shot.dialogue_ids,
        "character_ids": shot.character_ids,
        "prop_ids": shot.prop_ids,
        "shot_card": shot.shot_card,
        "image_prompt": shot.image_prompt,
        "video_prompt": shot.video_prompt,
        "negative_prompt": shot.negative_prompt,
    }
