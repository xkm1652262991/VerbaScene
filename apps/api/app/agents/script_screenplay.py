import json
import re
from typing import Any

from app.scripts.speaker_policy import strip_voice_only_scene_characters

def parse_screenplay_response(text: str) -> dict[str, Any]:
    payload = _loads_json_object(text)
    legacy_content = _string_or_default(payload.get("content"), "")
    scenes = normalize_production_scenes(
        payload.get("scenes"),
        legacy_content=legacy_content,
        legacy_dialogues=payload.get("dialogues"),
    )
    return {
        "content": render_readable_script(scenes),
        "scenes": scenes,
        "dialogues": extract_dialogues_from_scenes(scenes),
    }
def _chinese_scene_number(value: int) -> str:
    numbers = ("零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十")
    return numbers[value] if 0 <= value < len(numbers) else str(value)


def _loads_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("Script response JSON must be an object")
    return payload


def normalize_production_scenes(
    value: Any,
    *,
    legacy_content: str = "",
    legacy_dialogues: Any = None,
) -> list[dict[str, Any]]:
    """Normalize new production scenes and safely upgrade legacy script JSON.

    Nested scene dialogues are authoritative. Legacy top-level dialogues are only
    retained when their exact text is visible in the legacy readable screenplay;
    this prevents an audio-only dialogue track from surviving a migration.
    """
    if not isinstance(value, list) or not value:
        raise ValueError("Script response must contain non-empty scenes array")
    scenes: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Scene {index} must be an object")
        scene_no = _positive_int(item.get("scene_no"), index)
        visible_action = _string_or_default(
            item.get("visible_action"),
            _string_or_default(item.get("summary"), ""),
        )
        if not visible_action:
            raise ValueError(f"Scene {index} missing visible_action")
        scenes.append(
            {
                "scene_no": scene_no,
                "title": _string_or_default(item.get("title"), f"场景{index}"),
                "location": _string_or_default(item.get("location"), ""),
                "time_of_day": _string_or_default(item.get("time_of_day"), ""),
                "characters": _string_list(item.get("characters")),
                "props": _string_list(item.get("props")),
                "visible_action": visible_action,
                "story_purpose": _string_or_default(item.get("story_purpose"), visible_action),
                "start_state": _string_or_default(item.get("start_state"), ""),
                "end_state": _string_or_default(item.get("end_state"), ""),
                "mood": _string_or_default(item.get("mood"), ""),
                "source_evidence": _string_or_default(item.get("source_evidence"), ""),
                "inferred_elements": _string_list(item.get("inferred_elements")),
                "sound_cues": _string_list(item.get("sound_cues")),
                "dialogues": _normalize_dialogues(item.get("dialogues", []), scene_no=scene_no),
            }
        )

    scene_nos = [int(scene["scene_no"]) for scene in scenes]
    if len(scene_nos) != len(set(scene_nos)):
        raise ValueError("Script response contains duplicate scene_no values")
    if scene_nos != sorted(scene_nos):
        raise ValueError("Script response scene_no values must be in ascending order")

    _attach_grounded_legacy_dialogues(
        scenes,
        legacy_content=legacy_content,
        legacy_dialogues=legacy_dialogues,
    )
    return strip_voice_only_scene_characters(scenes)


def render_readable_script(scenes: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for index, scene in enumerate(scenes, start=1):
        scene_no = _positive_int(scene.get("scene_no"), index)
        title = _string_or_default(scene.get("title"), f"场景{index}")
        lines = [f"场景{_chinese_scene_number(scene_no)}：{title}"]
        location = _string_or_default(scene.get("location"), "")
        time_of_day = _string_or_default(scene.get("time_of_day"), "")
        if location or time_of_day:
            place_and_time = (
                f"地点：{location}" if location else "",
                f"时间：{time_of_day}" if time_of_day else "",
            )
            lines.append("｜".join(part for part in place_and_time if part))
        characters = _string_list(scene.get("characters"))
        if characters:
            lines.append(f"出场：{'、'.join(characters)}")
        props = _string_list(scene.get("props"))
        if props:
            lines.append(f"关键道具：{'、'.join(props)}")
        start_state = _string_or_default(scene.get("start_state"), "")
        end_state = _string_or_default(scene.get("end_state"), "")
        if start_state or end_state:
            lines.append(f"状态变化：{start_state or '未说明'} → {end_state or '未说明'}")
        lines.append(f"画面：{_string_or_default(scene.get('visible_action'), '')}")
        for dialogue in _normalize_dialogues(scene.get("dialogues", []), scene_no=scene_no):
            emotion = f"（{dialogue['emotion']}）" if dialogue["emotion"] else ""
            lines.append(f"{dialogue['speaker']}{emotion}：{dialogue['text']}")
            if dialogue.get("translation_zh"):
                lines.append(f"中文释义：{dialogue['translation_zh']}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def extract_dialogues_from_scenes(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dialogues: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes, start=1):
        scene_no = _positive_int(scene.get("scene_no"), index)
        dialogues.extend(_normalize_dialogues(scene.get("dialogues", []), scene_no=scene_no))
    return dialogues


def _normalize_dialogues(value: Any, *, scene_no: int | None = None) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    dialogues: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Dialogue {index} must be an object")
        text = _string_or_default(item.get("text"), "")
        if not text:
            continue
        if not re.search(r"[A-Za-z]", text):
            raise ValueError(f"Dialogue {index} must contain English text")
        speaker = _string_or_default(item.get("speaker"), "")
        if not speaker:
            raise ValueError(f"Dialogue {index} missing speaker")
        dialogue = {
            "id": _string_or_default(item.get("id"), ""),
            "speaker": speaker,
            "text": text,
            "translation_zh": _string_or_default(item.get("translation_zh"), ""),
            "emotion": _string_or_default(item.get("emotion"), ""),
            "source_type": _dialogue_source_type(item.get("source_type")),
            "sequence_order": _non_negative_int(item.get("sequence_order"), index - 1),
            "beat_id": _string_or_default(item.get("beat_id"), ""),
            "sound_cues": _string_list(item.get("sound_cues")),
        }
        resolved_scene_no = scene_no or _positive_int(item.get("scene_no"), 0)
        if resolved_scene_no > 0:
            dialogue["scene_no"] = resolved_scene_no
        dialogues.append(dialogue)
    return dialogues


def _attach_grounded_legacy_dialogues(
    scenes: list[dict[str, Any]],
    *,
    legacy_content: str,
    legacy_dialogues: Any,
) -> None:
    if not legacy_content or not isinstance(legacy_dialogues, list):
        return
    normalized = _normalize_dialogues(legacy_dialogues)
    existing = {
        (dialogue["speaker"], dialogue["text"])
        for scene in scenes
        for dialogue in scene["dialogues"]
    }
    for dialogue in normalized:
        if dialogue["text"] not in legacy_content:
            continue
        key = (dialogue["speaker"], dialogue["text"])
        if key in existing:
            continue
        scene_no = dialogue.get("scene_no") or _infer_scene_no_from_content(legacy_content, dialogue["text"])
        target = next((scene for scene in scenes if scene["scene_no"] == scene_no), None)
        if target is None:
            target = scenes[0]
        migrated = dict(dialogue)
        migrated["scene_no"] = target["scene_no"]
        target["dialogues"].append(migrated)
        existing.add(key)


def _infer_scene_no_from_content(content: str, dialogue_text: str) -> int:
    text_position = content.find(dialogue_text)
    if text_position < 0:
        return 1
    markers = list(re.finditer(r"场景(?:[零一二三四五六七八九十百]+|\d+)[：:]", content))
    return max(1, sum(1 for marker in markers if marker.start() <= text_position))


def _dialogue_source_type(value: Any) -> str:
    normalized = _string_or_default(value, "source").lower()
    return normalized if normalized in {"source", "adapted", "created"} else "source"


def _non_negative_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 0 else fallback


def _string_list(value: Any) -> list[str]:
    raw_items: list[Any]
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        raw_items = re.split(r"[,，、\n]+", value)
    else:
        return []
    items: list[str] = []
    for raw_item in raw_items:
        item = _string_or_default(raw_item, "")
        if item and item not in items:
            items.append(item)
    return items


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _string_or_default(value: Any, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, dict):
        parts = [
            f"{key}：{_string_or_default(item, '')}"
            for key, item in value.items()
            if _string_or_default(item, "")
        ]
        return "；".join(parts) or default
    if isinstance(value, list):
        parts = [_string_or_default(item, "") for item in value]
        return "、".join(item for item in parts if item) or default
    text = str(value).strip()
    return text or default
