from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.agents.avd_asset_strategy import avd_asset_metadata, build_avd_reference_strategy
from app.db.json_payload import compact_json_payload
from app.models import (
    Asset,
    Character,
    Prop,
    Scene,
    Shot,
)
from app.services.entity_service import list_characters

MAX_CHARACTER_SOURCE_REFERENCES = 2
MAX_VIDEO_CHARACTER_REFERENCES = 20
MAX_VIDEO_SCENE_REFERENCES = 1
MAX_VIDEO_IMAGE_REFERENCES = 22
VIDEO_REFERENCE_POLICY = "optional_first_frame_plus_20_characters_1_scene_v3"


@dataclass
class ResolvedReferenceAsset:
    asset_id: str
    uri: str
    asset_type: str
    asset_role: str | None
    entity_type: str | None
    entity_id: str | None
    reference_role: str
    priority: int
    asset_purpose: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "uri": self.uri,
            "asset_type": self.asset_type,
            "asset_role": self.asset_role,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "reference_role": self.reference_role,
            "priority": self.priority,
            "avd_asset_purpose": self.asset_purpose,
        }


@dataclass
class AssetResolution:
    stage: str
    project_id: str
    shot_id: str | None = None
    prompt_context: dict[str, Any] = field(default_factory=dict)
    reference_assets: list[ResolvedReferenceAsset] = field(default_factory=list)
    reference_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def references(self) -> list[str]:
        return [asset.uri for asset in sorted(self.reference_assets, key=lambda item: item.priority)]

    def to_task_payload(self) -> dict[str, Any]:
        return compact_json_payload({
            "stage": self.stage,
            "shot_id": self.shot_id,
            "prompt_context": self.prompt_context,
            "reference_assets": [asset.to_dict() for asset in sorted(self.reference_assets, key=lambda item: item.priority)],
            "reference_metadata": self.reference_metadata,
        })


def resolve_generation_assets(
    db: Session,
    project_id: str,
    *,
    stage: str,
    shot: Shot | None = None,
    visible_character_ids: list[str] | None = None,
    visible_prop_ids: list[str] | None = None,
    fallback_image_asset: Asset | None = None,
) -> AssetResolution:
    resolution = AssetResolution(
        stage=stage,
        project_id=project_id,
        shot_id=shot.id if shot else None,
    )

    if shot:
        resolution.prompt_context = _shot_prompt_context(db, project_id, shot)
    if stage == "reference_image":
        resolution.reference_metadata = {"reference_mode": "clean_reference"}
        return _finalize_resolution(resolution, shot=shot)

    if stage == "shot_image" and shot:
        character_ids = (
            _bounded_reference_ids(visible_character_ids, _id_list(shot.character_ids))
            if visible_character_ids is not None
            else _id_list(shot.character_ids)
        )
        prop_ids = (
            _bounded_reference_ids(visible_prop_ids, _id_list(shot.prop_ids))
            if visible_prop_ids is not None
            else _id_list(shot.prop_ids)
        )
        explicit_references = _add_explicit_entity_reference_assets(
            db,
            resolution,
            project_id,
            shot,
            start_priority=30,
        )
        if not explicit_references:
            _add_entity_reference_assets(
                db,
                resolution,
                project_id,
                shot,
                start_priority=30,
                character_ids=character_ids,
                prop_ids=prop_ids,
            )
        resolution.reference_metadata = {
            "reference_mode": "entity_reference",
            "reference_binding_source": "explicit" if explicit_references else "automatic",
            "visibility_source": "shot_card.image_prompts" if (
                visible_character_ids is not None or visible_prop_ids is not None
            ) else "legacy_all_bound_entities",
            "visible_character_ids": character_ids,
            "visible_prop_ids": prop_ids,
            "reference_asset_ids": [asset.asset_id for asset in resolution.reference_assets],
        }
        return _finalize_resolution(resolution, shot=shot)

    if stage == "shot_video" and shot:
        manual_reference_id = (
            shot.shot_card.get("video_reference_asset_id")
            if isinstance(shot.shot_card, dict)
            else None
        )
        first_frame = fallback_image_asset
        if isinstance(manual_reference_id, str) and manual_reference_id:
            manual_asset = db.get(Asset, manual_reference_id)
            if (
                manual_asset is not None
                and manual_asset.project_id == project_id
                and manual_asset.asset_type == "image"
                and manual_asset.status == "approved"
            ):
                first_frame = manual_asset
        if first_frame:
            resolution.reference_assets.append(
                _reference_asset(first_frame, "video_first_frame", 1)
            )
        explicit_references = _add_video_entity_reference_assets(
            db,
            resolution,
            project_id,
            shot,
            start_priority=30,
        )
        if not explicit_references:
            _add_automatic_video_entity_reference_assets(
                db,
                resolution,
                project_id,
                shot,
                start_priority=30,
            )
        unique_references: dict[str, ResolvedReferenceAsset] = {}
        for asset in resolution.reference_assets:
            unique_references.setdefault(asset.asset_id, asset)
        resolution.reference_assets = list(unique_references.values())
        reference_mode = (
            "manual_video_first_frame"
            if first_frame
            else "entity_references" if resolution.reference_assets else "none"
        )
        bound_reference_ids = _explicit_reference_asset_ids(shot)
        sent_reference_ids = [
            asset.asset_id
            for asset in resolution.reference_assets
            if asset.reference_role != "video_first_frame"
        ]
        resolution.reference_metadata = {
            "reference_mode": reference_mode,
            "reference_binding_source": "explicit" if explicit_references else "automatic",
            "video_reference_policy": VIDEO_REFERENCE_POLICY,
            "max_video_image_references": MAX_VIDEO_IMAGE_REFERENCES,
            "storyboard_first_frame_asset_id": (
                first_frame.id
                if first_frame and first_frame.asset_role == "shot_storyboard"
                else None
            ),
            "video_first_frame_asset_id": first_frame.id if first_frame else None,
            "video_first_frame_source": "manual" if first_frame else "none",
            "reference_asset_ids": sent_reference_ids,
            "bound_reference_asset_ids": bound_reference_ids,
            "omitted_video_reference_asset_ids": [
                asset_id
                for asset_id in bound_reference_ids
                if asset_id not in set(sent_reference_ids)
            ],
        }
        return _finalize_resolution(resolution, shot=shot)

    resolution.reference_metadata = {"reference_mode": "none"}
    return _finalize_resolution(resolution, shot=shot)


def _finalize_resolution(resolution: AssetResolution, *, shot: Shot | None) -> AssetResolution:
    risk_flags: list[str] = []
    if shot and isinstance(shot.shot_card, dict):
        risk_flags = [flag for flag in shot.shot_card.get("risk_flags", []) if isinstance(flag, str)]
    strategy = build_avd_reference_strategy(
        stage=resolution.stage,
        character_ids=_id_list(shot.character_ids) if shot else [],
        scene_id=shot.scene_id if shot else None,
        prop_ids=_id_list(shot.prop_ids) if shot else [],
        reference_assets=[asset.to_dict() for asset in resolution.reference_assets],
        risk_flags=risk_flags,
    )
    resolution.reference_metadata = {
        **resolution.reference_metadata,
        "avd_asset_strategy_version": strategy["version"],
        "avd_asset_pack": strategy["asset_pack"],
        "avd_asset_strategy": strategy,
    }
    return resolution


def _shot_prompt_context(db: Session, project_id: str, shot: Shot) -> dict[str, Any]:
    characters = _characters_for_shot(db, project_id, shot)
    scene = db.get(Scene, shot.scene_id) if shot.scene_id else None
    if scene and scene.project_id != project_id:
        scene = None
    props = _props_for_shot(db, project_id, shot)
    return {
        "characters": [
            {
                "id": character.id,
                "name": character.name,
                "fixed_prompt": character.fixed_prompt,
                "appearance": character.appearance,
                "identity": character.identity,
            }
            for character in characters
        ],
        "scene": {
            "id": scene.id,
            "name": scene.name,
            "fixed_prompt": scene.fixed_prompt,
            "description": scene.description,
            "visual_style": scene.visual_style,
        }
        if scene
        else None,
        "props": [
            {
                "id": prop.id,
                "name": prop.name,
                "visual_prompt": prop.visual_prompt,
                "description": prop.description,
            }
            for prop in props
        ],
    }


def _add_entity_reference_assets(
    db: Session,
    resolution: AssetResolution,
    project_id: str,
    shot: Shot,
    *,
    start_priority: int,
    character_ids: list[str] | None = None,
    prop_ids: list[str] | None = None,
) -> None:
    priority = start_priority
    for character_id in character_ids if character_ids is not None else _id_list(shot.character_ids):
        asset = _selected_asset(db, project_id, "image", "character_main_ref", "character", character_id)
        if asset:
            resolution.reference_assets.append(_reference_asset(asset, "character_main_ref", priority))
            priority += 1
            main_asset_id = asset.id
        else:
            main_asset_id = None
        for source_asset in _selected_assets_by_role_prefix(
            db,
            project_id,
            "image",
            "character_source_ref_",
            "character",
            character_id,
            exclude_asset_ids={main_asset_id} if main_asset_id else set(),
            limit=MAX_CHARACTER_SOURCE_REFERENCES,
        ):
            resolution.reference_assets.append(_reference_asset(source_asset, source_asset.asset_role or "character_source_ref", priority))
            priority += 1

    if shot.scene_id:
        asset = _selected_asset(db, project_id, "image", "scene_ref", "scene", shot.scene_id)
        if asset:
            resolution.reference_assets.append(_reference_asset(asset, "scene_ref", priority))
            priority += 1

    for prop_id in prop_ids if prop_ids is not None else _id_list(shot.prop_ids):
        asset = _selected_asset(db, project_id, "image", "prop_ref", "prop", prop_id)
        if asset:
            resolution.reference_assets.append(_reference_asset(asset, "prop_ref", priority))
            priority += 1


def _add_explicit_entity_reference_assets(
    db: Session,
    resolution: AssetResolution,
    project_id: str,
    shot: Shot,
    *,
    start_priority: int,
) -> bool:
    card = shot.shot_card if isinstance(shot.shot_card, dict) else {}
    if "reference_asset_ids" not in card:
        return False
    raw_ids = card.get("reference_asset_ids")
    asset_ids = raw_ids if isinstance(raw_ids, list) else []
    priority = start_priority
    for asset_id in asset_ids:
        if not isinstance(asset_id, str):
            continue
        asset = db.get(Asset, asset_id)
        if (
            asset is None
            or asset.project_id != project_id
            or asset.asset_type != "image"
            or asset.entity_type not in {"character", "scene", "prop"}
            or asset.status not in {"ready_for_review", "approved"}
        ):
            continue
        resolution.reference_assets.append(
            _reference_asset(
                asset,
                asset.asset_role or f"{asset.entity_type}_ref",
                priority,
            )
        )
        priority += 1
    return True


def _add_video_entity_reference_assets(
    db: Session,
    resolution: AssetResolution,
    project_id: str,
    shot: Shot,
    *,
    start_priority: int,
) -> bool:
    """Resolve explicit video references without forwarding prop or redundant images."""

    card = shot.shot_card if isinstance(shot.shot_card, dict) else {}
    if "reference_asset_ids" not in card:
        return False

    priority = start_priority
    character_count = 0
    scene_count = 0
    seen_entities: set[tuple[str, str]] = set()
    for asset_id in _explicit_reference_asset_ids(shot):
        asset = db.get(Asset, asset_id)
        if (
            asset is None
            or asset.project_id != project_id
            or asset.asset_type != "image"
            or asset.entity_type not in {"character", "scene"}
            or not asset.entity_id
            or asset.status not in {"ready_for_review", "approved"}
        ):
            continue

        entity_key = (asset.entity_type, asset.entity_id)
        if entity_key in seen_entities:
            continue
        if asset.entity_type == "character":
            if character_count >= MAX_VIDEO_CHARACTER_REFERENCES:
                continue
            character_count += 1
        else:
            if scene_count >= MAX_VIDEO_SCENE_REFERENCES:
                continue
            scene_count += 1

        seen_entities.add(entity_key)
        resolution.reference_assets.append(
            _reference_asset(
                asset,
                asset.asset_role or f"{asset.entity_type}_ref",
                priority,
            )
        )
        priority += 1

    return True


def _add_automatic_video_entity_reference_assets(
    db: Session,
    resolution: AssetResolution,
    project_id: str,
    shot: Shot,
    *,
    start_priority: int,
) -> None:
    """Use one adopted version per subject; prop and multi-angle images stay out."""

    priority = start_priority
    for character_id in _id_list(shot.character_ids)[:MAX_VIDEO_CHARACTER_REFERENCES]:
        asset = _selected_asset(
            db,
            project_id,
            "image",
            "character_main_ref",
            "character",
            character_id,
        )
        if asset:
            resolution.reference_assets.append(
                _reference_asset(asset, "character_main_ref", priority)
            )
            priority += 1

    if shot.scene_id:
        asset = _selected_asset(
            db,
            project_id,
            "image",
            "scene_ref",
            "scene",
            shot.scene_id,
        )
        if asset:
            resolution.reference_assets.append(
                _reference_asset(asset, "scene_ref", priority)
            )


def _explicit_reference_asset_ids(shot: Shot) -> list[str]:
    card = shot.shot_card if isinstance(shot.shot_card, dict) else {}
    raw_ids = card.get("reference_asset_ids")
    if not isinstance(raw_ids, list):
        return []
    return list(dict.fromkeys(item for item in raw_ids if isinstance(item, str)))


def _bounded_reference_ids(value: Any, allowed: list[str]) -> list[str]:
    if not isinstance(value, list):
        return []
    allowed_set = set(allowed)
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item in allowed_set))


def _selected_asset(
    db: Session,
    project_id: str,
    asset_type: str,
    asset_role: str,
    entity_type: str,
    entity_id: str,
) -> Asset | None:
    statement = (
        select(Asset)
        .where(Asset.project_id == project_id)
        .where(Asset.asset_type == asset_type)
        .where(Asset.asset_role == asset_role)
        .where(Asset.entity_type == entity_type)
        .where(Asset.entity_id == entity_id)
        .where(Asset.is_selected.is_(True))
        .where(Asset.status.in_(["ready_for_review", "approved"]))
        .order_by(Asset.version.desc(), Asset.created_at.desc())
    )
    if entity_type == "character" and asset_role == "character_main_ref":
        statement = statement.where(
            or_(Asset.variant_key.is_(None), Asset.variant_key == "base")
        )
    return db.scalar(statement)


def _selected_assets_by_role_prefix(
    db: Session,
    project_id: str,
    asset_type: str,
    asset_role_prefix: str,
    entity_type: str,
    entity_id: str,
    *,
    exclude_asset_ids: set[str] | None = None,
    limit: int | None = None,
) -> list[Asset]:
    exclude_asset_ids = exclude_asset_ids or set()
    rows = list(
        db.scalars(
            select(Asset)
            .where(Asset.project_id == project_id)
            .where(Asset.asset_type == asset_type)
            .where(Asset.asset_role.startswith(asset_role_prefix))
            .where(Asset.entity_type == entity_type)
            .where(Asset.entity_id == entity_id)
            .where(Asset.is_selected.is_(True))
            .where(Asset.status.in_(["ready_for_review", "approved"]))
            .order_by(Asset.asset_role.asc(), Asset.version.desc(), Asset.created_at.desc())
        ).all()
    )
    filtered = [asset for asset in rows if asset.id not in exclude_asset_ids]
    if limit is not None:
        return filtered[: max(0, limit)]
    return filtered


def _reference_asset(asset: Asset, reference_role: str, priority: int) -> ResolvedReferenceAsset:
    usage = avd_asset_metadata(
        asset_type=asset.asset_type,
        asset_role=asset.asset_role,
        entity_type=asset.entity_type,
        raw_response=asset.raw_response,
    )
    return ResolvedReferenceAsset(
        asset_id=asset.id,
        uri=asset.uri,
        asset_type=asset.asset_type,
        asset_role=asset.asset_role,
        entity_type=asset.entity_type,
        entity_id=asset.entity_id,
        reference_role=reference_role,
        priority=priority,
        asset_purpose=usage["asset_purpose"],
    )


def _characters_for_shot(db: Session, project_id: str, shot: Shot) -> list[Character]:
    current_visual_ids = {character.id for character in list_characters(db, project_id)}
    characters = []
    for character_id in _id_list(shot.character_ids):
        if character_id not in current_visual_ids:
            continue
        character = db.get(Character, character_id)
        if character and character.project_id == project_id:
            characters.append(character)
    return characters


def _props_for_shot(db: Session, project_id: str, shot: Shot) -> list[Prop]:
    props = []
    for prop_id in _id_list(shot.prop_ids):
        prop = db.get(Prop, prop_id)
        if prop and prop.project_id == project_id:
            props.append(prop)
    return props


def _id_list(values: list) -> list[str]:
    return [value for value in values if isinstance(value, str)]
