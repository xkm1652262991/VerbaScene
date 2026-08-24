from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.style import compose_image_prompt, resolve_visual_style
from app.models import Project, Shot
from app.services.asset_resolver_service import AssetResolution, resolve_generation_assets


def build_project_prompt_previews(
    db: Session,
    project_id: str,
    *,
    approved_only: bool = False,
) -> list[dict[str, Any]]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    statement = (
        select(Shot)
        .where(Shot.project_id == project_id)
        .where(Shot.is_current.is_(True))
        .order_by(Shot.shot_no)
    )
    if approved_only:
        statement = statement.where(Shot.status == "approved")
    return [_build_shot_prompt_preview(db, project, shot) for shot in db.scalars(statement).all()]


def assert_project_pre_image_requirements(
    db: Session,
    project_id: str,
    *,
    shot_ids: set[str] | None = None,
) -> None:
    previews = build_project_prompt_previews(db, project_id)
    if shot_ids is not None:
        previews = [item for item in previews if item["shot_id"] in shot_ids]
    blockers = [
        {"shot_id": item["shot_id"], "shot_no": item["shot_no"], "blockers": item["blockers"]}
        for item in previews
        if item["blockers"]
    ]
    if blockers:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "图片生成前检查未通过，请先修复阻断项", "blockers": blockers},
        )


def _build_shot_prompt_preview(db: Session, project: Project, shot: Shot) -> dict[str, Any]:
    prompt_data = _storyboard_prompt_data(
        shot,
        style=resolve_visual_style(project.style, project.creative_settings),
    )
    resolution = resolve_generation_assets(
        db,
        project.id,
        stage="shot_image",
        shot=shot,
        visible_character_ids=prompt_data["visible_character_ids"],
        visible_prop_ids=prompt_data["visible_prop_ids"],
    )
    blockers = _blockers(prompt_data)
    warnings = _warnings(shot=shot, prompt_data=prompt_data, resolution=resolution)
    return {
        "shot_id": shot.id,
        "shot_no": shot.shot_no,
        "description": shot.description,
        "image_prompt": prompt_data["positive_prompt"],
        "negative_prompt": prompt_data["negative_prompt"],
        "provider_profile": "由图片节点当前选择决定",
        "reference_assets": [asset.to_dict() for asset in resolution.reference_assets],
        "compile_notes": [],
        "blockers": blockers,
        "warnings": warnings,
        "can_generate": not blockers,
    }


def _storyboard_prompt_data(shot: Shot, *, style: str) -> dict[str, Any]:
    card = shot.shot_card if isinstance(shot.shot_card, dict) else {}
    prompts = card.get("image_prompts") if isinstance(card.get("image_prompts"), dict) else {}
    item = prompts.get("shot_storyboard") if isinstance(prompts.get("shot_storyboard"), dict) else {}
    positive = item.get("positive_prompt")
    if not isinstance(positive, str) or not positive.strip():
        positive = shot.image_prompt
    negative = item.get("negative_prompt") or shot.negative_prompt
    return {
        "positive_prompt": compose_image_prompt(
            positive.strip() if isinstance(positive, str) else "",
            style,
        ),
        "negative_prompt": negative.strip() if isinstance(negative, str) and negative.strip() else None,
        "visible_character_ids": _bounded_ids(item.get("visible_character_ids"), shot.character_ids),
        "visible_prop_ids": _bounded_ids(item.get("visible_prop_ids"), shot.prop_ids),
    }


def _blockers(prompt_data: dict[str, Any]) -> list[str]:
    return [] if prompt_data["positive_prompt"] else ["缺少可用于生成图片的 Prompt"]


def _warnings(
    *,
    shot: Shot,
    prompt_data: dict[str, Any],
    resolution: AssetResolution,
) -> list[str]:
    warnings: list[str] = []
    reference_roles = {asset.reference_role for asset in resolution.reference_assets}
    risk_flags = [
        item
        for item in (shot.shot_card.get("risk_flags") if isinstance(shot.shot_card, dict) else []) or []
        if isinstance(item, str)
    ]
    if len(prompt_data["visible_character_ids"]) > 1:
        warnings.append("多角色镜头，注意身份混淆")
    if "long_duration" in risk_flags:
        warnings.append("长镜头，后续图生视频失败风险较高")
    if "complex_motion" in risk_flags or "fast_motion" in risk_flags:
        warnings.append("复杂动作，建议拆分或降低动作幅度")
    if prompt_data["visible_character_ids"] and "character_main_ref" not in reference_roles:
        warnings.append("缺少已选角色参考图，将仅依据 Prompt 生成")
    if shot.scene_id and "scene_ref" not in reference_roles:
        warnings.append("缺少已选场景参考图，将仅依据 Prompt 生成")
    if prompt_data["visible_prop_ids"] and "prop_ref" not in reference_roles:
        warnings.append("缺少已选道具参考图，将仅依据 Prompt 生成")
    return warnings


def _bounded_ids(value: Any, allowed: list) -> list[str]:
    allowed_ids = list(dict.fromkeys(item for item in allowed if isinstance(item, str)))
    if not isinstance(value, list):
        return allowed_ids
    allowed_set = set(allowed_ids)
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item in allowed_set))
