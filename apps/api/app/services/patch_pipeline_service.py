from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Asset, Dialogue, Project, Shot, ShotFramePrompt


AVD_PATCH_PIPELINE_VERSION = "avd-patch-pipeline-v1"


def affected_stages_for_patch(*, target_type: str, asset_type: str | None = None) -> list[str]:
    normalized = target_type.strip().lower()
    if normalized in {"project", "style", "input"}:
        return ["script", "assets", "production", "export"]
    if normalized in {"script", "chapter"}:
        return ["assets", "production", "export"]
    if normalized in {"character", "scene", "prop", "entity"}:
        return ["assets", "production", "export"]
    if normalized == "shot":
        return ["production", "export"]
    if normalized == "dialogue":
        return ["production", "export"]
    if normalized == "asset":
        return _stages_for_asset_type(asset_type)
    return ["production", "export"]


def analyze_patch_impact(
    db: Session,
    project_id: str,
    *,
    target_type: str,
    target_id: str | None = None,
    change_summary: str | None = None,
    field: str | None = None,
) -> dict[str, Any]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    normalized_type = target_type.strip().lower()
    asset_type: str | None = None
    source_asset: Asset | None = None
    if normalized_type == "asset":
        if not target_id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="target_id is required for asset patches")
        source_asset = db.get(Asset, target_id)
        if source_asset is None or source_asset.project_id != project_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")
        asset_type = source_asset.asset_type

    shots = _affected_shots(db, project_id, normalized_type, target_id, source_asset)
    shot_ids = [shot["id"] for shot in shots]
    assets = _affected_assets(db, project_id, normalized_type, target_id, source_asset, shot_ids)
    stages = affected_stages_for_patch(target_type=normalized_type, asset_type=asset_type)

    return {
        "engine": AVD_PATCH_PIPELINE_VERSION,
        "project_id": project_id,
        "target": {
            "type": normalized_type,
            "id": target_id,
            "field": field,
            "change_summary": change_summary,
        },
        "affected_shots": shots,
        "affected_assets": assets,
        "affected_stages": stages,
        "recommended_actions": _recommended_actions(normalized_type, stages),
        "requires_human_review": True,
    }


def _affected_shots(
    db: Session,
    project_id: str,
    target_type: str,
    target_id: str | None,
    source_asset: Asset | None,
) -> list[dict[str, Any]]:
    if target_type == "asset" and source_asset is not None:
        target_type = source_asset.entity_type or source_asset.asset_type
        target_id = source_asset.entity_id

    current_shots = _current_shots(db, project_id)
    if target_type in {"project", "style", "input", "script", "chapter"}:
        return [_shot_payload(shot, "全局变更影响当前分镜") for shot in current_shots]
    if target_type == "character" and target_id:
        return [
            _shot_payload(shot, "镜头绑定该角色")
            for shot in current_shots
            if target_id in _id_list(shot.character_ids)
        ]
    if target_type == "scene" and target_id:
        return [_shot_payload(shot, "镜头绑定该场景") for shot in current_shots if shot.scene_id == target_id]
    if target_type == "prop" and target_id:
        return [_shot_payload(shot, "镜头绑定该道具") for shot in current_shots if target_id in _id_list(shot.prop_ids)]
    if target_type == "shot" and target_id:
        return _shot_with_context(current_shots, target_id)
    if target_type == "dialogue" and target_id:
        dialogue = db.get(Dialogue, target_id)
        if dialogue and dialogue.project_id == project_id and dialogue.shot_id:
            return _shot_with_context(current_shots, dialogue.shot_id)
    return []


def _affected_assets(
    db: Session,
    project_id: str,
    target_type: str,
    target_id: str | None,
    source_asset: Asset | None,
    shot_ids: list[str],
) -> list[dict[str, Any]]:
    clauses = []
    if target_type in {"character", "scene", "prop"} and target_id:
        clauses.append((Asset.entity_type == target_type) & (Asset.entity_id == target_id))
    if target_type == "asset" and source_asset is not None:
        clauses.append(Asset.id == source_asset.id)
        if source_asset.entity_type in {"character", "scene", "prop"} and source_asset.entity_id:
            clauses.append((Asset.entity_type == source_asset.entity_type) & (Asset.entity_id == source_asset.entity_id))
    if shot_ids:
        clauses.append((Asset.entity_type == "shot") & (Asset.entity_id.in_(shot_ids)))
        frame_prompt_ids = _frame_prompt_ids(db, project_id, shot_ids)
        if frame_prompt_ids:
            clauses.append((Asset.entity_type == "shot_frame") & (Asset.entity_id.in_(frame_prompt_ids)))
        dialogue_ids = _dialogue_ids(db, project_id, shot_ids)
        if dialogue_ids:
            clauses.append((Asset.entity_type == "dialogue") & (Asset.entity_id.in_(dialogue_ids)))
    if target_type in {"project", "style", "input", "script", "chapter"}:
        clauses.append(Asset.project_id == project_id)
    if not clauses:
        return []

    rows = list(
        db.scalars(
            select(Asset)
            .where(Asset.project_id == project_id)
            .where(or_(*clauses))
            .order_by(Asset.asset_type, Asset.entity_type, Asset.entity_id, Asset.version.desc())
        ).all()
    )
    seen: set[str] = set()
    payloads: list[dict[str, Any]] = []
    for asset in rows:
        if asset.id in seen:
            continue
        seen.add(asset.id)
        payloads.append(
            {
                "id": asset.id,
                "asset_type": asset.asset_type,
                "asset_role": asset.asset_role,
                "entity_type": asset.entity_type,
                "entity_id": asset.entity_id,
                "version": asset.version,
                "is_selected": asset.is_selected,
                "status": asset.status,
                "reason": _asset_impact_reason(asset, shot_ids),
            }
        )
    return payloads


def _recommended_actions(target_type: str, stages: list[str]) -> list[str]:
    actions = ["先确认影响范围，再执行局部重生成"]
    if target_type in {"character", "scene", "prop", "asset"}:
        actions.append("重新生成或上传对应实体参考资产候选")
    if "production" in stages:
        actions.append("检查受影响片段并按需重新编译最终视频 Prompt")
        actions.append("只重生成受影响镜头的分镜首帧候选")
        actions.append("只重生成受影响镜头的视频候选")
    if "export" in stages:
        actions.append("确认资源后重新合成导出")
    return list(dict.fromkeys(actions))


def _current_shots(db: Session, project_id: str) -> list[Shot]:
    return list(
        db.scalars(
            select(Shot)
            .where(Shot.project_id == project_id)
            .where(Shot.is_current.is_(True))
            .order_by(Shot.shot_no)
        ).all()
    )


def _shot_with_context(shots: list[Shot], shot_id: str) -> list[dict[str, Any]]:
    for index, shot in enumerate(shots):
        if shot.id != shot_id:
            continue
        window = shots[max(0, index - 1): index + 2]
        payloads = []
        for item in window:
            reason = "直接修改镜头" if item.id == shot_id else "连续性三镜窗口"
            payloads.append(_shot_payload(item, reason))
        return payloads
    return []


def _shot_payload(shot: Shot, reason: str) -> dict[str, Any]:
    return {
        "id": shot.id,
        "shot_no": shot.shot_no,
        "scene_id": shot.scene_id,
        "character_ids": _id_list(shot.character_ids),
        "prop_ids": _id_list(shot.prop_ids),
        "reason": reason,
    }


def _frame_prompt_ids(db: Session, project_id: str, shot_ids: list[str]) -> list[str]:
    return [
        row
        for row in db.scalars(
            select(ShotFramePrompt.id)
            .where(ShotFramePrompt.project_id == project_id)
            .where(ShotFramePrompt.shot_id.in_(shot_ids))
        ).all()
        if row
    ]


def _dialogue_ids(db: Session, project_id: str, shot_ids: list[str]) -> list[str]:
    return [
        row
        for row in db.scalars(
            select(Dialogue.id)
            .where(Dialogue.project_id == project_id)
            .where(Dialogue.shot_id.in_(shot_ids))
        ).all()
        if row
    ]


def _asset_impact_reason(asset: Asset, shot_ids: list[str]) -> str:
    if asset.entity_type == "shot" and asset.entity_id in shot_ids:
        return "镜头产物需要随镜头变更重评"
    if asset.entity_type == "shot_frame":
        return "该历史帧图属于受影响镜头；当前链路只保留分镜图作为可选首帧候选"
    if asset.entity_type == "dialogue":
        return "对白属于受影响片段，视频 Prompt 和导出字幕可能过期"
    return "资产与变更目标直接关联"


def _stages_for_asset_type(asset_type: str | None) -> list[str]:
    if asset_type == "image":
        return ["production", "export"]
    if asset_type == "video":
        return ["export"]
    if asset_type in {"audio", "subtitle"}:
        return []
    if asset_type == "final_video":
        return ["export"]
    return ["assets", "production", "export"]


def _id_list(values: list) -> list[str]:
    return [value for value in values if isinstance(value, str)]
