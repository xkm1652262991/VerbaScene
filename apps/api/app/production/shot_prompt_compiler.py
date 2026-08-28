from __future__ import annotations

import hashlib
import json
from typing import Any

from app.agents import build_shot_video_prompt
from app.agents.style import resolve_visual_style
from app.models import Character, Dialogue, Project, Prop, Scene, Shot


VIDEO_PROMPT_COMPILER_VERSION = "seedance2-shot-first-v6"


def apply_video_prompt_field(
    *,
    project: Project,
    data: dict[str, Any],
    characters: list[Character],
    scenes: list[Scene],
    props: list[Prop],
    dialogues: list[Dialogue] | None = None,
    reference_variant_keys: dict[str, str] | None = None,
    reference_media_labels: dict[str, str] | None = None,
    storyboard_media_label: str | None = None,
) -> None:
    """Compile the deterministic video prompt and annotate its source contract."""

    data["video_prompt"] = build_shot_video_prompt(
        description=str(data.get("description") or ""),
        camera_shot=data.get("camera_shot"),
        camera_movement=data.get("camera_movement"),
        characters=select_entities(characters, data.get("character_ids")),
        props=select_entities(props, data.get("prop_ids")),
        dialogues=dialogues or [],
        scene=select_scene(scenes, data.get("scene_id")),
        shot_card=data.get("shot_card") if isinstance(data.get("shot_card"), dict) else {},
        aspect_ratio=project.aspect_ratio,
        resolution=project.resolution,
        animation_style=resolve_visual_style(project.style, project.creative_settings),
        reference_variant_keys=reference_variant_keys,
        reference_media_labels=reference_media_labels,
        storyboard_media_label=storyboard_media_label,
    )
    card = dict(data.get("shot_card") or {})
    card["prompt_source"] = "compiled"
    card["prompt_compiler_version"] = VIDEO_PROMPT_COMPILER_VERSION
    card["prompt_stale"] = False
    data["shot_card"] = card


def shot_prompt_fingerprint(
    *,
    project: Project,
    shot: Shot,
    characters: list[Character],
    scene: Scene | None,
    props: list[Prop],
    dialogues: list[Dialogue],
) -> str:
    payload = {
        "prompt_compiler_version": VIDEO_PROMPT_COMPILER_VERSION,
        "project": {
            "aspect_ratio": project.aspect_ratio,
            "resolution": project.resolution,
            "style": project.style,
            "creative_settings": project.creative_settings,
        },
        "shot": {
            "id": shot.id,
            "description": shot.description,
            "camera_shot": shot.camera_shot,
            "camera_movement": shot.camera_movement,
            "shot_card": _prompt_source_card(shot.shot_card),
            "dialogue_ids": shot.dialogue_ids,
        },
        "characters": [
            {"id": item.id, "asset_spec": item.asset_spec}
            for item in characters
            if item.id in set(shot.character_ids or [])
        ],
        "scene": {"id": scene.id, "asset_spec": scene.asset_spec} if scene else None,
        "props": [{"id": item.id, "asset_spec": item.asset_spec} for item in props],
        "dialogues": [
            {
                "id": item.id,
                "text": item.text,
                "translation_zh": item.translation_zh,
                "emotion": item.emotion,
                "beat_id": item.beat_id,
                "sound_cues": item.sound_cues,
            }
            for item in dialogues
        ],
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"


def select_entities(items: list[Any], selected_ids: Any) -> list[Any]:
    wanted = {item for item in selected_ids or [] if isinstance(item, str)}
    return [item for item in items if item.id in wanted]


def select_scene(scenes: list[Scene], scene_id: Any) -> Scene | None:
    return next((scene for scene in scenes if scene.id == scene_id), None)


def _prompt_source_card(value: object) -> dict[str, Any]:
    card = dict(value) if isinstance(value, dict) else {}
    for key in (
        "prompt_fingerprint",
        "prompt_stale",
        "stale_reason",
        "prompt_source",
        "prompt_compiler_version",
        "prompt_compiled_at",
    ):
        card.pop(key, None)
    card["beats"] = _without_legacy_beat_timing(card.get("beats"))
    if isinstance(card.get("motion_timing"), dict):
        motion_timing = dict(card["motion_timing"])
        motion_timing["beats"] = _without_legacy_beat_timing(motion_timing.get("beats"))
        card["motion_timing"] = motion_timing
    return card


def _without_legacy_beat_timing(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        beat = dict(item)
        for key in ("duration_sec", "time", "range", "at"):
            beat.pop(key, None)
        result.append(beat)
    return result
