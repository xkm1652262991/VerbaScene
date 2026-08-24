import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, AssetCandidate, Character, Project, Prop, Scene, Shot
from app.schemas.readiness import ProjectReadinessRead, ProjectReadinessSummaryRead, ReadinessCheckRead, ShotReadinessRead
from app.services.asset_repository import list_current_assets


READY_ASSET_STATUSES = {"ready_for_review", "approved"}
def get_project_readiness(db: Session, project_id: str) -> ProjectReadinessRead:
    project = db.get(Project, project_id)
    if project is None:
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    shots = _current_shots(db, project_id)
    assets = list_current_assets(db, project_id)
    candidates = _pending_candidates(db, project_id)
    characters = _entity_status_map(db, Character, project_id)
    character_names = _entity_name_map(db, Character, project_id)
    scenes = _entity_status_map(db, Scene, project_id)
    props = _entity_status_map(db, Prop, project_id)
    shot_readiness = [
        _shot_readiness(
            shot=shot,
            assets=assets,
            candidates=candidates,
            characters=characters,
            character_names=character_names,
            scenes=scenes,
            props=props,
        )
        for shot in shots
    ]
    return ProjectReadinessRead(
        project_id=project_id,
        summary=ProjectReadinessSummaryRead(
            shot_count=len(shot_readiness),
            image_ready_count=sum(1 for item in shot_readiness if item.ready_for_image),
            video_ready_count=sum(1 for item in shot_readiness if item.ready_for_video),
            image_blocker_count=sum(len(item.image_blockers) for item in shot_readiness),
            video_blocker_count=sum(len(item.video_blockers) for item in shot_readiness),
        ),
        shots=shot_readiness,
    )


def _shot_readiness(
    *,
    shot: Shot,
    assets: list[Asset],
    candidates: list[AssetCandidate],
    characters: dict[str, str],
    character_names: dict[str, str],
    scenes: dict[str, str],
    props: dict[str, str],
) -> ShotReadinessRead:
    pending_image_candidate_count = _pending_image_candidate_count(candidates, shot)
    pending_video_candidate_count = _pending_candidate_count(
        candidates,
        asset_type="video",
        entity_type="shot",
        entity_ids={shot.id},
    )
    checks = [
        _check("description_ready", "镜头描述", bool((shot.description or "").strip()), "镜头描述为空"),
        _check("image_prompt_ready", "图像 Prompt", bool((shot.image_prompt or "").strip()), "图像 Prompt 为空"),
        _check("video_prompt_ready", "视频 Prompt", bool((shot.video_prompt or "").strip()), "视频 Prompt 为空"),
        _check("duration_ready", "镜头时长", shot.duration_sec is not None and shot.duration_sec > 0, "镜头时长未设置"),
        _character_binding_check(shot, characters, character_names),
        _single_entity_check("场景绑定", shot.scene_id, scenes, "场景"),
        _entity_check("道具绑定", shot.prop_ids, props, "道具", allow_empty=True),
        _selected_asset_check("角色参考图", assets, "image", "character", set(shot.character_ids or []), "镜头绑定角色但缺少已选角色参考图"),
        _selected_asset_check("场景参考图", assets, "image", "scene", {shot.scene_id} if shot.scene_id else set(), "镜头绑定场景但缺少已选场景参考图"),
        _optional_video_first_frame_check(assets, shot),
        _pending_candidate_check(
            "pending_image_candidates_resolved",
            "图片候选审核",
            pending_image_candidate_count,
            "图片候选",
            "确认或拒绝后，视频阶段才会使用最新正式资产",
        ),
        _pending_candidate_check(
            "pending_video_candidates_resolved",
            "视频候选审核",
            pending_video_candidate_count,
            "视频候选",
            "可采用或拒绝这些候选；不影响其他工作区编辑",
        ),
    ]
    image_keys = {
        "image_prompt_ready",
    }
    video_keys = {
        "duration_ready",
        "video_prompt_ready",
        "video_first_frame_ready",
    }
    image_blockers = _messages(checks, image_keys)
    video_blockers = _messages(checks, video_keys)
    return ShotReadinessRead(
        shot_id=shot.id,
        shot_no=shot.shot_no,
        ready_for_image=not image_blockers,
        ready_for_video=not video_blockers,
        checks=checks,
        image_blockers=image_blockers,
        video_blockers=video_blockers,
    )


def _check(key: str, label: str, ok: bool, message: str, *, severity: str = "blocker") -> ReadinessCheckRead:
    return ReadinessCheckRead(
        key=key,
        label=label,
        ok=ok,
        message="已就绪" if ok else message,
        severity=severity,
    )


def _entity_check(label: str, ids: list, status_by_id: dict[str, str], entity_label: str, *, allow_empty: bool = False) -> ReadinessCheckRead:
    ids = [str(item) for item in (ids or []) if str(item).strip()]
    missing = [item for item in ids if item not in status_by_id]
    ok = (allow_empty and not ids) or (bool(ids) and not missing)
    key = {
        "角色": "characters_ready",
        "道具": "props_ready",
    }.get(entity_label, f"{entity_label}_ready")
    if not ids and not allow_empty:
        return _check(key, label, False, f"镜头未绑定{entity_label}")
    return _check(key, label, ok, f"存在不存在的{entity_label}：{len(missing)} 个")


def _character_binding_check(shot: Shot, status_by_id: dict[str, str], names_by_id: dict[str, str]) -> ReadinessCheckRead:
    ids = [str(item) for item in (shot.character_ids or []) if str(item).strip()]
    missing = [item for item in ids if item not in status_by_id]
    if ids:
        return _check("characters_ready", "角色绑定", not missing, f"存在不存在的角色：{len(missing)} 个")
    if _shot_mentions_known_character(shot, names_by_id):
        return _check("characters_ready", "角色绑定", False, "镜头疑似包含角色但未绑定角色")
    return _check("characters_ready", "角色绑定", True, "纯场景/道具镜头，无需绑定角色")


def _shot_mentions_known_character(shot: Shot, names_by_id: dict[str, str]) -> bool:
    text_parts = [
        shot.description or "",
        shot.image_prompt or "",
        shot.video_prompt or "",
        " ".join(str(item) for item in (shot.dialogue_ids or [])),
    ]
    if isinstance(shot.shot_card, dict):
        text_parts.append(json.dumps(shot.shot_card, ensure_ascii=False))
    haystack = "\n".join(text_parts)
    return any(name and name in haystack for name in names_by_id.values())


def _single_entity_check(label: str, entity_id: str | None, status_by_id: dict[str, str], entity_label: str) -> ReadinessCheckRead:
    ok = bool(entity_id) and str(entity_id) in status_by_id
    return _check("scene_ready", label, ok, f"镜头未绑定{entity_label}")


def _selected_asset_check(
    label: str,
    assets: list[Asset],
    asset_type: str,
    entity_type: str,
    entity_ids: set[str | None],
    message: str,
) -> ReadinessCheckRead:
    ids = {item for item in entity_ids if item}
    if not ids:
        return _check(f"{entity_type}_reference_ready", label, True, "无需参考图", severity="warning")
    selected_ids = {
        asset.entity_id
        for asset in assets
        if asset.asset_type == asset_type
        and asset.entity_type == entity_type
        and asset.is_selected
        and asset.status in READY_ASSET_STATUSES
    }
    missing = ids - selected_ids
    return _check(f"{entity_type}_reference_ready", label, not missing, message, severity="warning")


def _optional_video_first_frame_check(assets: list[Asset], shot: Shot) -> ReadinessCheckRead:
    reference_id = (
        shot.shot_card.get("video_reference_asset_id")
        if isinstance(shot.shot_card, dict)
        else None
    )
    if not isinstance(reference_id, str) or not reference_id:
        return _check(
            "video_first_frame_ready",
            "视频首帧",
            True,
            "默认不使用首帧",
        )
    ok = any(
        asset.id == reference_id
        and asset.asset_type == "image"
        and asset.status in READY_ASSET_STATUSES
        for asset in assets
    )
    return _check(
        "video_first_frame_ready",
        "视频首帧",
        ok,
        "明确选择的视频首帧不存在或尚未采用",
    )


def _pending_candidate_check(key: str, label: str, count: int, candidate_label: str, hint: str) -> ReadinessCheckRead:
    return _check(key, label, count == 0, f"还有 {count} 个待审{candidate_label}，{hint}")


def _pending_image_candidate_count(
    candidates: list[AssetCandidate],
    shot: Shot,
) -> int:
    total = _pending_candidate_count(
        candidates,
        asset_type="image",
        entity_type="shot",
        entity_ids={shot.id},
    )
    total += _pending_candidate_count(
        candidates,
        asset_type="image",
        entity_type="character",
        entity_ids={str(item) for item in (shot.character_ids or []) if str(item).strip()},
    )
    total += _pending_candidate_count(
        candidates,
        asset_type="image",
        entity_type="scene",
        entity_ids={shot.scene_id} if shot.scene_id else set(),
    )
    total += _pending_candidate_count(
        candidates,
        asset_type="image",
        entity_type="prop",
        entity_ids={str(item) for item in (shot.prop_ids or []) if str(item).strip()},
    )
    return total


def _pending_candidate_count(
    candidates: list[AssetCandidate],
    *,
    asset_type: str,
    entity_type: str,
    entity_ids: set[str | None],
) -> int:
    ids = {item for item in entity_ids if item}
    if not ids:
        return 0
    return sum(
        1
        for candidate in candidates
        if candidate.status == "pending_review"
        and candidate.asset_type == asset_type
        and candidate.entity_type == entity_type
        and candidate.entity_id in ids
    )


def _messages(checks: list[ReadinessCheckRead], keys: set[str]) -> list[str]:
    return [check.message for check in checks if check.key in keys and not check.ok and check.severity == "blocker"]


def _pending_candidates(db: Session, project_id: str) -> list[AssetCandidate]:
    return list(
        db.scalars(
            select(AssetCandidate)
            .where(AssetCandidate.project_id == project_id)
            .where(AssetCandidate.status == "pending_review")
        ).all()
    )


def _current_shots(db: Session, project_id: str) -> list[Shot]:
    return list(
        db.scalars(
            select(Shot)
            .where(Shot.project_id == project_id)
            .where(Shot.is_current.is_(True))
            .order_by(Shot.shot_no, Shot.created_at)
        ).all()
    )


def _entity_status_map(db: Session, model: type, project_id: str) -> dict[str, str]:
    rows = db.execute(select(model.id, model.status).where(model.project_id == project_id)).all()
    return {str(row[0]): str(row[1]) for row in rows}


def _entity_name_map(db: Session, model: type, project_id: str) -> dict[str, str]:
    rows = db.execute(select(model.id, model.name).where(model.project_id == project_id)).all()
    return {str(row[0]): str(row[1]) for row in rows}
