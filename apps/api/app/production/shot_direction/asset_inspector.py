from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from app.platform.media import MediaStore
from app.production.shot_direction.contracts import ShotDirectionInput


class AssetInspector(Protocol):
    inspection_level: str

    def inspect(self, source: ShotDirectionInput) -> dict: ...


class VisualAssetInspector(AssetInspector, Protocol):
    """Reserved VLM boundary. No pixel-reading implementation is registered yet."""


class MetadataAssetInspector:
    inspection_level = "metadata_only"

    def __init__(self, media_store: MediaStore) -> None:
        self.media_store = media_store

    def inspect(self, source: ShotDirectionInput) -> dict:
        conflicts: list[dict] = []
        inspected_assets: list[dict] = []
        adopted_by_key: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)

        for asset in source.adopted_assets:
            entity_type = _text(asset.get("entity_type"))
            entity_id = _text(asset.get("entity_id"))
            role = _text(asset.get("asset_role"))
            variant_key = _text(asset.get("variant_key")) or "base"
            key = (entity_type, entity_id, role, variant_key)
            adopted_by_key[key].append(asset)
            uri = _text(asset.get("uri"))
            local_checked = _is_local_uri(uri)
            local_resolvable: bool | None = None
            if not uri:
                conflicts.append(
                    {
                        "code": "asset_uri_missing",
                        "asset_id": _text(asset.get("id")),
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "asset_role": role,
                        "message": "采用资产缺少媒体 URI。",
                    }
                )
            elif local_checked:
                try:
                    self.media_store.resolve_local_path(uri)
                    local_resolvable = True
                except (FileNotFoundError, OSError, ValueError) as exc:
                    local_resolvable = False
                    conflicts.append(
                        {
                            "code": "local_media_unresolvable",
                            "asset_id": _text(asset.get("id")),
                            "entity_type": entity_type,
                            "entity_id": entity_id,
                            "asset_role": role,
                            "message": f"本地采用资产无法解析：{exc}",
                        }
                    )
            if _text(asset.get("status")) not in {"ready_for_review", "approved"}:
                conflicts.append(
                    {
                        "code": "adopted_asset_status_conflict",
                        "asset_id": _text(asset.get("id")),
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "asset_role": role,
                        "message": "资产被标记为当前采用版本，但状态不可用于生成。",
                    }
                )
            inspected_assets.append(
                {
                    "asset_id": _text(asset.get("id")),
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "asset_role": role,
                    "variant_key": variant_key,
                    "version": asset.get("version"),
                    "uri": uri,
                    "mime_type": asset.get("mime_type"),
                    "width": asset.get("width"),
                    "height": asset.get("height"),
                    "local_file_checked": local_checked,
                    "local_file_resolvable": local_resolvable,
                }
            )

        for key, assets in adopted_by_key.items():
            if len(assets) <= 1:
                continue
            conflicts.append(
                {
                    "code": "multiple_adopted_versions",
                    "entity_type": key[0],
                    "entity_id": key[1],
                    "asset_role": key[2],
                    "variant_key": key[3],
                    "asset_ids": [_text(item.get("id")) for item in assets],
                    "message": "同一实体、用途和状态变体存在多个当前采用版本。",
                }
            )

        missing_references: list[dict] = []
        for character in source.characters:
            entity_id = _text(character.get("id"))
            if not _has_reference(adopted_by_key, "character", entity_id, "character_main_ref"):
                missing_references.append(
                    {
                        "entity_type": "character",
                        "entity_id": entity_id,
                        "name": _text(character.get("name")),
                        "recommended_asset_role": "character_main_ref",
                        "blocking": False,
                    }
                )
        for scene in source.scenes:
            entity_id = _text(scene.get("id"))
            if not _has_reference(adopted_by_key, "scene", entity_id, "scene_ref"):
                missing_references.append(
                    {
                        "entity_type": "scene",
                        "entity_id": entity_id,
                        "name": _text(scene.get("name")),
                        "recommended_asset_role": "scene_ref",
                        "blocking": False,
                    }
                )

        planning_ready = bool(source.characters and source.scenes)
        blocking_conflicts = [item for item in conflicts if _blocks_default_reference_path(item)]
        generation_ready = planning_ready and not missing_references and not blocking_conflicts
        return {
            "planning_ready": planning_ready,
            "generation_ready": generation_ready,
            "inspection_level": self.inspection_level,
            "missing_references": missing_references,
            "conflicts": conflicts,
            "inspected_assets": inspected_assets,
            "notes": [
                "本轮只检查设定、采用版本、URI 与媒体元数据，未读取图片像素。",
                "状态变体作为文本事实进入分镜，不要求独立状态图。",
                "道具不属于默认参考图硬要求。",
            ],
        }


def _has_reference(
    adopted_by_key: dict[tuple[str, str, str, str], list[dict]],
    entity_type: str,
    entity_id: str,
    role: str,
) -> bool:
    return any(
        key[0] == entity_type and key[1] == entity_id and key[2] == role and bool(assets)
        for key, assets in adopted_by_key.items()
    )


def _blocks_default_reference_path(conflict: dict) -> bool:
    return (
        (conflict.get("entity_type"), conflict.get("asset_role"))
        in {("character", "character_main_ref"), ("scene", "scene_ref")}
    )


def _is_local_uri(uri: str) -> bool:
    if not uri:
        return False
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return True
    if parsed.scheme:
        return False
    return uri.startswith("/") or Path(uri).is_absolute()


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""
