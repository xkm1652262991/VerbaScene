from __future__ import annotations

from typing import Any


AVD_ASSET_STRATEGY_VERSION = "avd-asset-strategy-v2"

VIDEO_ASSET_ROLES = {
    "character_main_ref",
    "scene_ref",
    "prop_ref",
    "shot_storyboard",
}

DISPLAY_ASSET_ROLES = {
    "shot_storyboard_grid",
    "full_board",
    "quick_board",
    "mood_board",
    "character_board",
    "scene_board",
    "world_board",
}

CONSISTENCY_ASSET_ROLES = {
    "character_face_ref",
    "character_full_body_ref",
    "character_expression_ref",
    "character_turnaround_ref",
    "scene_multi_angle_ref",
    "scene_layout_ref",
    "scene_panorama_ref",
}

MARKETING_ASSET_ROLES = {
    "poster",
    "cover",
    "marketing_banner",
}

KNOWN_PURPOSES = {
    "video_asset",
    "display_asset",
    "consistency_asset",
    "marketing_asset",
    "runtime_asset",
    "unknown",
}

VIDEO_REFERENCE_PURPOSES = {"video_asset", "consistency_asset"}
DISPLAY_ONLY_PURPOSES = {"display_asset", "marketing_asset"}


def infer_avd_asset_purpose(
    *,
    asset_type: str,
    asset_role: str | None,
    entity_type: str | None = None,
    raw_response: dict[str, Any] | None = None,
) -> str:
    explicit = _explicit_asset_purpose(raw_response)
    if explicit:
        return explicit

    role = (asset_role or "").strip()
    if asset_type == "image":
        if role in VIDEO_ASSET_ROLES:
            return "video_asset"
        if role in DISPLAY_ASSET_ROLES:
            return "display_asset"
        if role in CONSISTENCY_ASSET_ROLES:
            return "consistency_asset"
        if role in MARKETING_ASSET_ROLES:
            return "marketing_asset"
        if entity_type in {"character", "scene", "prop", "shot", "shot_frame"}:
            return "video_asset"
    if asset_type == "video":
        return "runtime_asset"
    if asset_type in {"audio", "subtitle", "final_video"}:
        return "runtime_asset"
    return "unknown"


def avd_asset_metadata(
    *,
    asset_type: str,
    asset_role: str | None,
    entity_type: str | None = None,
    raw_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    purpose = infer_avd_asset_purpose(
        asset_type=asset_type,
        asset_role=asset_role,
        entity_type=entity_type,
        raw_response=raw_response,
    )
    return {
        "version": AVD_ASSET_STRATEGY_VERSION,
        "asset_purpose": purpose,
        "can_use_as_video_reference": purpose in VIDEO_REFERENCE_PURPOSES,
        "display_only": purpose in DISPLAY_ONLY_PURPOSES,
    }


def build_avd_reference_strategy(
    *,
    stage: str,
    character_ids: list[str] | None = None,
    scene_id: str | None = None,
    prop_ids: list[str] | None = None,
    reference_assets: list[dict[str, Any]] | None = None,
    risk_flags: list[str] | None = None,
) -> dict[str, Any]:
    characters = _clean_ids(character_ids)
    props = _clean_ids(prop_ids)
    references = [_reference_payload(item) for item in reference_assets or []]
    reference_roles = {item["reference_role"] for item in references if item.get("reference_role")}
    blockers: list[str] = []
    warnings: list[str] = []
    requirements: list[dict[str, Any]] = []

    if stage == "shot_image":
        for character_id in characters:
            _require_entity_reference(
                requirements,
                blockers,
                references,
                entity_type="character",
                entity_id=character_id,
                reference_role="character_main_ref",
                label="角色参考图",
                severity="error",
            )
        if scene_id:
            _require_entity_reference(
                requirements,
                blockers,
                references,
                entity_type="scene",
                entity_id=scene_id,
                reference_role="scene_ref",
                label="场景参考图",
                severity="error",
            )
        for prop_id in props:
            _require_entity_reference(
                requirements,
                warnings,
                references,
                entity_type="prop",
                entity_id=prop_id,
                reference_role="prop_ref",
                label="道具参考图",
                severity="warning",
            )

    if stage == "shot_video":
        for character_id in characters:
            _require_entity_reference(
                requirements,
                warnings,
                references,
                entity_type="character",
                entity_id=character_id,
                reference_role="character_main_ref",
                label="角色一致性参考图",
                severity="warning",
            )
        if scene_id:
            _require_entity_reference(
                requirements,
                warnings,
                references,
                entity_type="scene",
                entity_id=scene_id,
                reference_role="scene_ref",
                label="场景一致性参考图",
                severity="warning",
            )
        if len(characters) >= 2 and "scene_ref" not in reference_roles:
            warnings.append("多角色镜头缺少场景空间锚点，容易出现站位和视线混乱")

    for reference in references:
        if stage == "shot_video" and not reference["can_use_as_video_reference"]:
            blockers.append(
                f"{reference['reference_role'] or reference['asset_role'] or reference['asset_id']} 是展示/营销用途资产，不能直接作为视频参考"
            )

    return {
        "version": AVD_ASSET_STRATEGY_VERSION,
        "stage": stage,
        "asset_pack": _asset_pack(stage, character_count=len(characters), prop_count=len(props), risk_flags=risk_flags or []),
        "requirements": requirements,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)),
        "reference_purposes": references,
    }


def extract_avd_reference_strategy(raw_response: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw_response, dict):
        return None
    metadata = raw_response.get("request_metadata")
    if not isinstance(metadata, dict):
        return None
    resolution = metadata.get("asset_resolution")
    if not isinstance(resolution, dict):
        return None
    reference_metadata = resolution.get("reference_metadata")
    if not isinstance(reference_metadata, dict):
        return None
    strategy = reference_metadata.get("avd_asset_strategy")
    return strategy if isinstance(strategy, dict) else None


def _explicit_asset_purpose(raw_response: dict[str, Any] | None) -> str | None:
    if not isinstance(raw_response, dict):
        return None
    candidates: list[Any] = [
        raw_response.get("avd_asset_purpose"),
        raw_response.get("asset_purpose"),
    ]
    metadata = raw_response.get("request_metadata")
    if isinstance(metadata, dict):
        candidates.extend(
            [
                metadata.get("avd_asset_purpose"),
                metadata.get("asset_purpose"),
            ]
        )
        usage = metadata.get("avd_asset_usage")
        if isinstance(usage, dict):
            candidates.append(usage.get("asset_purpose"))
    for value in candidates:
        if isinstance(value, str) and value in KNOWN_PURPOSES:
            return value
    return None


def _reference_payload(reference: dict[str, Any]) -> dict[str, Any]:
    explicit_purpose = reference.get("avd_asset_purpose")
    purpose = (
        explicit_purpose
        if isinstance(explicit_purpose, str) and explicit_purpose in KNOWN_PURPOSES
        else infer_avd_asset_purpose(
            asset_type=str(reference.get("asset_type") or ""),
            asset_role=reference.get("asset_role") if isinstance(reference.get("asset_role"), str) else None,
            entity_type=reference.get("entity_type") if isinstance(reference.get("entity_type"), str) else None,
        )
    )
    return {
        "asset_id": reference.get("asset_id"),
        "asset_role": reference.get("asset_role"),
        "entity_type": reference.get("entity_type"),
        "entity_id": reference.get("entity_id"),
        "reference_role": reference.get("reference_role"),
        "asset_purpose": purpose,
        "can_use_as_video_reference": purpose in VIDEO_REFERENCE_PURPOSES,
    }


def _require_entity_reference(
    requirements: list[dict[str, Any]],
    messages: list[str],
    references: list[dict[str, Any]],
    *,
    entity_type: str,
    entity_id: str,
    reference_role: str,
    label: str,
    severity: str,
) -> None:
    satisfied = any(
        reference.get("reference_role") == reference_role
        and reference.get("entity_type") == entity_type
        and reference.get("entity_id") == entity_id
        for reference in references
    )
    requirements.append(
        {
            "label": label,
            "reference_role": reference_role,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "severity": severity,
            "satisfied": satisfied,
        }
    )
    if not satisfied:
        messages.append(f"缺少{label}: {entity_type}:{entity_id}")


def _require_any_reference(
    requirements: list[dict[str, Any]],
    messages: list[str],
    references: list[dict[str, Any]],
    *,
    reference_roles: set[str],
    label: str,
    severity: str,
) -> None:
    satisfied = any(reference.get("reference_role") in reference_roles for reference in references)
    requirements.append(
        {
            "label": label,
            "reference_roles": sorted(reference_roles),
            "severity": severity,
            "satisfied": satisfied,
        }
    )
    if not satisfied:
        messages.append(f"缺少{label}")


def _asset_pack(stage: str, *, character_count: int, prop_count: int, risk_flags: list[str]) -> str:
    if stage == "shot_video":
        if character_count >= 3:
            return "formal_group_video_pack"
        if {"complex_motion", "fast_motion"} & set(risk_flags):
            return "action_video_pack"
        if character_count == 2:
            return "relationship_video_pack"
        if prop_count:
            return "prop_continuity_video_pack"
        return "standard_video_pack"
    if stage == "shot_image":
        if character_count >= 3:
            return "group_storyboard_pack"
        if prop_count:
            return "prop_storyboard_pack"
        return "standard_storyboard_pack"
    return "clean_reference_pack"


def _clean_ids(values: list[str] | None) -> list[str]:
    return [value for value in values or [] if isinstance(value, str) and value.strip()]
