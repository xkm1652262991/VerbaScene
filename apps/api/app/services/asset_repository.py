"""Database queries, version allocation, and provenance helpers for assets."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Asset,
    AssetCandidate,
    Character,
    Dialogue,
    Project,
    ProjectStageRun,
    Prop,
    Scene,
    Shot,
    ShotFrameImage,
    ShotFramePrompt,
)
from app.services.entity_service import get_current_entities

def list_assets(
    db: Session,
    project_id: str,
    asset_type: str | None = None,
    entity_type: str | None = None,
) -> list[Asset]:
    statement = select(Asset).where(Asset.project_id == project_id)
    if asset_type:
        statement = statement.where(Asset.asset_type == asset_type)
    if entity_type:
        statement = statement.where(Asset.entity_type == entity_type)
    return list(
        db.scalars(
            statement.order_by(
                Asset.is_selected.desc(),
                Asset.created_at.desc(),
                Asset.entity_type,
                Asset.entity_id,
                Asset.version.desc(),
            ),
        ).all()
    )


def list_assets_page(
    db: Session,
    project_id: str,
    asset_type: str | None = None,
    entity_type: str | None = None,
    offset: int = 0,
    limit: int = 500,
) -> tuple[list[Asset], int]:
    statement = select(Asset).where(Asset.project_id == project_id)
    count_statement = select(func.count(Asset.id)).where(Asset.project_id == project_id)
    if asset_type:
        statement = statement.where(Asset.asset_type == asset_type)
        count_statement = count_statement.where(Asset.asset_type == asset_type)
    if entity_type:
        statement = statement.where(Asset.entity_type == entity_type)
        count_statement = count_statement.where(Asset.entity_type == entity_type)
    ordered = statement.order_by(
        Asset.is_selected.desc(),
        Asset.created_at.desc(),
        Asset.entity_type,
        Asset.entity_id,
        Asset.version.desc(),
    )
    total = db.scalar(count_statement) or 0
    assets = list(db.scalars(ordered.offset(offset).limit(limit)).all())
    return assets, total


def list_current_assets(
    db: Session,
    project_id: str,
    asset_type: str | None = None,
    entity_type: str | None = None,
) -> list[Asset]:
    assets = list_assets(db, project_id, asset_type=asset_type, entity_type=entity_type)
    current_characters, current_scenes, current_props = get_current_entities(db, project_id)
    current_entity_ids = {
        "character": {item.id for item in current_characters},
        "scene": {item.id for item in current_scenes},
        "prop": {item.id for item in current_props},
    }
    current_shots = _current_shots(db, project_id)
    current_shot_ids = {shot.id for shot in current_shots}
    current_shot_batch_ids = {shot.shot_batch_id for shot in current_shots if shot.shot_batch_id}
    current_frame_asset_ids = _current_frame_asset_ids(db, project_id, current_shot_ids)
    current_dialogue_ids = _current_dialogue_ids(db, project_id, current_shot_ids)

    filtered = [
        asset for asset in assets
        if _is_current_asset(
            asset,
            current_entity_ids=current_entity_ids,
            current_shot_ids=current_shot_ids,
            current_shot_batch_ids=current_shot_batch_ids,
            current_frame_asset_ids=current_frame_asset_ids,
            current_dialogue_ids=current_dialogue_ids,
        )
    ]
    return filtered


def get_project_or_404(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def resolve_shot_video_input_asset(
    db: Session,
    shot: Shot,
    *,
    fallback_image_asset: Asset | None = None,
) -> Asset | None:
    manual_asset_id = (
        shot.shot_card.get("video_reference_asset_id")
        if isinstance(shot.shot_card, dict)
        else None
    )
    if isinstance(manual_asset_id, str) and manual_asset_id:
        manual_asset = db.get(Asset, manual_asset_id)
        if (
            manual_asset is not None
            and manual_asset.project_id == shot.project_id
            and manual_asset.asset_type == "image"
            and manual_asset.status == "approved"
        ):
            return manual_asset

    if fallback_image_asset is not None:
        return fallback_image_asset
    return None


def get_selected_assets(db: Session, project_id: str, entity_type: str) -> list[Asset]:
    return list(
        db.scalars(
            select(Asset)
            .where(Asset.project_id == project_id)
            .where(Asset.asset_type == "image")
            .where(Asset.entity_type == entity_type)
            .where(Asset.is_selected.is_(True))
            .order_by(Asset.created_at),
        ).all()
    )


def get_selected_assets_by_type(
    db: Session,
    project_id: str,
    asset_type: str,
    entity_type: str,
) -> list[Asset]:
    return list(
        db.scalars(
            select(Asset)
            .where(Asset.project_id == project_id)
            .where(Asset.asset_type == asset_type)
            .where(Asset.entity_type == entity_type)
            .where(Asset.is_selected.is_(True))
            .order_by(Asset.created_at),
        ).all()
    )


def next_asset_version(
    db: Session,
    project_id: str,
    asset_type: str,
    entity_type: str | None,
    entity_id: str | None,
    asset_role: str | None = None,
    variant_key: str | None = None,
) -> int:
    statement = (
        select(func.coalesce(func.max(Asset.version), 0))
        .where(Asset.project_id == project_id)
        .where(Asset.asset_type == asset_type)
        .where(Asset.asset_role == asset_role)
        .where(Asset.entity_type == entity_type)
        .where(Asset.entity_id == entity_id)
        .where(Asset.variant_key == variant_key)
    )
    current = db.scalar(statement)
    return int(current or 0) + 1


def next_candidate_version(
    db: Session,
    project_id: str,
    asset_type: str,
    entity_type: str | None,
    entity_id: str | None,
    asset_role: str | None = None,
    variant_key: str | None = None,
) -> int:
    asset_next = next_asset_version(
        db,
        project_id,
        asset_type,
        entity_type,
        entity_id,
        asset_role,
        variant_key,
    )
    candidate_current = db.scalar(
        select(func.coalesce(func.max(AssetCandidate.version), 0))
        .where(AssetCandidate.project_id == project_id)
        .where(AssetCandidate.asset_type == asset_type)
        .where(AssetCandidate.asset_role == asset_role)
        .where(AssetCandidate.entity_type == entity_type)
        .where(AssetCandidate.entity_id == entity_id)
        .where(AssetCandidate.variant_key == variant_key)
    )
    return max(asset_next, int(candidate_current or 0) + 1)


def infer_asset_role(*, asset_type: str, entity_type: str | None, entity_id: str | None = None) -> str | None:
    if asset_type == "image":
        return {
            "character": "character_main_ref",
            "scene": "scene_ref",
            "prop": "prop_ref",
            "shot": "shot_storyboard",
            "grid": "shot_storyboard_grid",
        }.get(entity_type or "")
    if asset_type == "video" and entity_type == "shot":
        return "shot_video"
    if asset_type in {"export", "final_video"}:
        return "final_export"
    return None


def ensure_asset_target_exists(db: Session, project_id: str, entity_type: str, entity_id: str) -> None:
    model_by_entity = {
        "project": Project,
        "character": Character,
        "scene": Scene,
        "prop": Prop,
        "shot": Shot,
    }
    model = model_by_entity.get(entity_type)
    if model is None:
        return
    row = db.get(model, entity_id)
    if row is None or getattr(row, "project_id", None) != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset target not found")


def _current_shots(db: Session, project_id: str) -> list[Shot]:
    return list(
        db.scalars(
            select(Shot)
            .where(Shot.project_id == project_id)
            .where(Shot.is_current.is_(True))
            .order_by(Shot.shot_no, Shot.created_at)
        ).all()
    )


def _current_frame_asset_ids(db: Session, project_id: str, current_shot_ids: set[str]) -> set[str]:
    if not current_shot_ids:
        return set()
    rows = db.scalars(
        select(ShotFrameImage)
        .where(ShotFrameImage.project_id == project_id)
        .where(ShotFrameImage.shot_id.in_(current_shot_ids))
        .where(ShotFrameImage.asset_id.is_not(None))
    ).all()
    return {row.asset_id for row in rows if row.asset_id}


def _current_dialogue_ids(db: Session, project_id: str, current_shot_ids: set[str]) -> set[str]:
    if not current_shot_ids:
        return set()
    rows = db.scalars(
        select(Dialogue.id)
        .where(Dialogue.project_id == project_id)
        .where(Dialogue.shot_id.in_(current_shot_ids))
    ).all()
    return {row for row in rows if row}


def _is_current_asset(
    asset: Asset,
    *,
    current_entity_ids: dict[str, set[str]],
    current_shot_ids: set[str],
    current_shot_batch_ids: set[str],
    current_frame_asset_ids: set[str],
    current_dialogue_ids: set[str],
) -> bool:
    if asset.source_shot_batch_id and asset.source_shot_batch_id in current_shot_batch_ids:
        return True
    if asset.entity_type in current_entity_ids:
        return asset.entity_id in current_entity_ids[asset.entity_type]
    if asset.entity_type == "shot":
        return asset.entity_id in current_shot_ids
    if asset.entity_type == "shot_frame":
        return asset.id in current_frame_asset_ids
    if asset.entity_type == "dialogue":
        return asset.entity_id in current_dialogue_ids
    if asset.asset_type == "final_video":
        return True
    if asset.entity_type in {None, "project"}:
        return True
    return False


def asset_source_context(db: Session, entity_type: str | None, entity_id: str | None) -> tuple[str | None, str | None]:
    if entity_type == "shot" and entity_id:
        shot = db.get(Shot, entity_id)
        if shot:
            return shot.script_id, shot.shot_batch_id
    if entity_type == "shot_frame" and entity_id:
        frame_prompt = db.get(ShotFramePrompt, entity_id)
        if frame_prompt:
            shot = db.get(Shot, frame_prompt.shot_id)
            if shot:
                return shot.script_id, shot.shot_batch_id
    return None, None


def resolve_source_stage_run_id(db: Session, project_id: str, source_task_id: str | None) -> str | None:
    if not source_task_id:
        return None
    run = db.scalar(
        select(ProjectStageRun)
        .where(ProjectStageRun.project_id == project_id)
        .where(ProjectStageRun.task_id == source_task_id)
        .order_by(ProjectStageRun.created_at.desc())
    )
    return run.id if run else None
