import re
from typing import Any

from app.agents.camera_language import professional_movement, professional_shot_size
from app.agents.style import (
    DEFAULT_VISUAL_STYLE,
    image_style_dna,
    resolve_visual_style,
)
from app.models import Character, Dialogue, Prop, Scene


def build_shot_video_prompt(
    *,
    description: str,
    camera_shot: str | None,
    camera_movement: str | None,
    characters: list[Character],
    scene: Scene | None,
    props: list[Prop] | None = None,
    dialogues: list[Dialogue] | None = None,
    shot_card: dict[str, Any] | None = None,
    aspect_ratio: str = "16:9",
    resolution: str = "854x480",
    animation_style: str = "",
    reference_variant_keys: dict[str, str] | None = None,
    reference_media_labels: dict[str, str] | None = None,
    storyboard_media_label: str | None = None,
) -> str:
    """Compile a compact, shot-first video prompt.

    The provider request carries aspect ratio, resolution and duration as
    parameters.  The prompt therefore only describes the creative content:
    reference bindings, ordered internal shots, dialogue, sound and a small
    set of continuity constraints.
    """

    # Kept in the signature for compatibility; these belong in provider
    # params, not in the creative prompt.
    _ = aspect_ratio, resolution
    card = shot_card if isinstance(shot_card, dict) else {}
    beats = _normalized_beats(card, description)
    dialogue_by_id = {item.id: item for item in dialogues or []}
    media_labels = reference_media_labels or {}
    source_text = _prompt_source_text(
        description=description,
        card=card,
        dialogues=dialogues or [],
    )
    used_props = [
        prop
        for prop in props or []
        if _prop_is_used(prop, source_text)
    ]

    lines = [
        "生成连续动画英语短剧片段，使用原生英文对白、环境音和动作音效。",
    ]

    reference_binding = _reference_binding(
        characters=characters,
        scene=scene,
        props=used_props,
        reference_variant_keys=reference_variant_keys or {},
        reference_media_labels=media_labels,
        storyboard_media_label=storyboard_media_label,
    )
    if reference_binding:
        lines.append(reference_binding)

    style_text = _compact_visual_style(animation_style)
    if style_text:
        lines.append(f"视觉风格：{style_text}；优先沿用参考图的具体画风、材质和光线。")

    shot_blocks: list[list[str]] = []
    bound_dialogue_ids: set[str] = set()
    shot_action = card.get("action") if isinstance(card.get("action"), dict) else {}
    start_state = _first_text(shot_action.get("start_state"))
    end_state = _first_text(shot_action.get("end_state"))
    for index, beat in enumerate(beats, start=1):
        camera = _first_text(
            beat.get("camera"),
            beat.get("camera_language"),
            card.get("camera"),
            f"{professional_shot_size(camera_shot or '中景')}，"
            f"{professional_movement(camera_movement or '固定机位')}",
        )
        action = _first_text(beat.get("action"), beat.get("description"), description)
        if index == 1 and start_state and start_state not in action:
            action = f"起始：{start_state}；{action}"
        if index == len(beats) and end_state and end_state not in action:
            action = f"{action}；收束：{end_state}"
        block = [f"镜头{index}：{camera}。{action}"]
        beat_dialogues: list[Dialogue] = []

        for dialogue_id in _string_list(beat.get("dialogue_ids")):
            dialogue = dialogue_by_id.get(dialogue_id)
            if dialogue is None:
                continue
            bound_dialogue_ids.add(dialogue.id)
            beat_dialogues.append(dialogue)
        if beat_dialogues:
            block.append(f"对白：{_dialogue_notation(beat_dialogues)}")

        sound_cues = _string_list(beat.get("sound_cues"))
        sound_cues = _unique_strings(
            [
                *[cue for dialogue in beat_dialogues for cue in _string_list(dialogue.sound_cues)],
                *sound_cues,
            ]
        )
        if sound_cues:
            block.append(f"音效：{_sound_notation(sound_cues)}")
        shot_blocks.append(block)

    unbound_dialogues = [
        item
        for item in dialogues or []
        if item.id not in bound_dialogue_ids
    ]
    if unbound_dialogues:
        if shot_blocks:
            shot_blocks[-1].append(f"对白：{_dialogue_notation(unbound_dialogues)}")
        else:
            shot_blocks.append([f"镜头1：对白：{_dialogue_notation(unbound_dialogues)}"])

    for block in shot_blocks:
        lines.extend(block)

    lines.extend(
        [
            "全局约束：对白发音清楚、语速自然，口型与说话人同步；环境音和动作音效紧跟动作且不盖住对白。"
            "保持角色身份、外观、服装、身体比例、场景空间、光线和道具状态连续；动作有过渡，切镜有动机。",
            "不新增角色、道具或剧情；画面不得出现字幕、标题、对白气泡、标签、logo、水印或任何其他可读文字。"
            "Narrator 若存在，仅作为画外音，不额外生成独立旁白段落。",
        ]
    )
    return "\n".join(line for line in lines if line.strip())


def _normalized_beats(card: dict[str, Any], description: str) -> list[dict[str, Any]]:
    raw = card.get("beats")
    if not isinstance(raw, list):
        motion_timing = card.get("motion_timing")
        if isinstance(motion_timing, dict):
            raw = motion_timing.get("beats")
    if not isinstance(raw, list):
        raw = []
    beats = [dict(item) for item in raw if isinstance(item, dict)]
    if beats:
        return beats
    camera = card.get("camera") if isinstance(card.get("camera"), dict) else {}
    action = card.get("action") if isinstance(card.get("action"), dict) else {}
    return [
        {
            "beat_id": "beat-1",
            "camera": "，".join(
                item
                for item in (
                    _first_text(camera.get("shot_size")),
                    _first_text(camera.get("angle")),
                    _first_text(camera.get("movement")),
                )
                if item
            ),
            "action": _first_text(action.get("main_action"), description),
            "dialogue_ids": [],
            "sound_cues": [],
        }
    ]


def _reference_binding(
    *,
    characters: list[Character],
    scene: Scene | None,
    props: list[Prop],
    reference_variant_keys: dict[str, str],
    reference_media_labels: dict[str, str],
    storyboard_media_label: str | None,
) -> str:
    bindings: list[str] = []
    if storyboard_media_label:
        bindings.append(f"{storyboard_media_label}=当前片段首帧")
    for character in characters:
        name = str(character.name or "角色").strip()
        media_label = reference_media_labels.get(character.id)
        if media_label:
            bindings.append(
                f"{media_label}=角色“{name}”"
                + _variant_suffix(character, reference_variant_keys.get(character.id))
            )
    if scene:
        scene_name = str(scene.name or "当前场景").strip()
        scene_media_label = reference_media_labels.get(scene.id)
        if scene_media_label:
            bindings.append(
                f"{scene_media_label}=场景“{scene_name}”"
                + _variant_suffix(scene, reference_variant_keys.get(scene.id))
            )
    for prop in props:
        prop_name = str(prop.name or "道具").strip()
        prop_media_label = reference_media_labels.get(prop.id)
        if prop_media_label:
            bindings.append(
                f"{prop_media_label}=道具“{prop_name}”"
                + _variant_suffix(prop, reference_variant_keys.get(prop.id))
            )
    if not bindings:
        return ""
    return (
        "参考绑定："
        + "；".join(bindings)
        + "。沿用参考图中的身份、外观、空间、材质和光线，不重复改写素材描述。"
    )


def _variant_suffix(entity: Character | Scene | Prop, variant_key: str | None) -> str:
    variant_name = _variant_name(entity, variant_key)
    return f"（状态：{variant_name}）" if variant_name else ""


def _variant_name(entity: Character | Scene | Prop, variant_key: str | None) -> str:
    if not variant_key or variant_key == "base":
        return ""
    asset_spec = getattr(entity, "asset_spec", None)
    if not isinstance(asset_spec, dict):
        return ""
    variants = asset_spec.get("state_variants")
    if not isinstance(variants, list):
        return ""
    for item in variants:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or item.get("variant_key") or "").strip()
        if key == variant_key:
            return str(item.get("name") or item.get("label") or variant_key).strip()
    return ""


def _prompt_source_text(
    *,
    description: str,
    card: dict[str, Any],
    dialogues: list[Dialogue],
) -> str:
    parts: list[str] = [description]
    for key in ("story_purpose", "emotional_intent"):
        parts.append(_first_text(card.get(key)))
    for key in ("action", "frame_plan", "storyboard_frame"):
        value = card.get(key)
        if isinstance(value, dict):
            parts.extend(str(item) for item in value.values())
    for beat in _normalized_beats(card, description):
        parts.extend(
            [
                _first_text(beat.get("action"), beat.get("description")),
                *_string_list(beat.get("sound_cues")),
            ]
        )
    for dialogue in dialogues:
        parts.extend([dialogue.text, dialogue.speaker_name])
        parts.extend(_string_list(dialogue.sound_cues))
    return "；".join(item.strip() for item in parts if str(item or "").strip())


def _prop_is_used(prop: Prop, source_text: str) -> bool:
    source = source_text.casefold()
    aliases = _prop_aliases(prop)
    return any(alias.casefold() in source for alias in aliases if len(alias.strip()) >= 2)


def _prop_aliases(prop: Prop) -> set[str]:
    name = str(getattr(prop, "name", "") or "").strip()
    aliases: set[str] = {name} if name else set()
    asset_spec = getattr(prop, "asset_spec", None)
    if isinstance(asset_spec, dict):
        for key in ("alias", "aliases", "short_name", "display_name", "name_aliases"):
            value = asset_spec.get(key)
            if isinstance(value, list):
                aliases.update(str(item).strip() for item in value if str(item).strip())
            elif isinstance(value, str) and value.strip():
                aliases.add(value.strip())
    compact_name = re.sub(r"[\s·•/、，,；;：:]+", "", name)
    if compact_name:
        aliases.add(compact_name)
        for size in (2, 3, 4):
            if len(compact_name) >= size:
                # Chinese asset names are commonly referred to by their
                # distinctive suffix (彩车、幕布、徽章), not by every
                # arbitrary n-gram in the full name.
                aliases.add(compact_name[-size:])
    aliases.update(
        item.strip()
        for item in re.split(r"[与和及\s/、，,；;：:]", name)
        if len(item.strip()) >= 2
    )
    return aliases


def _compact_visual_style(style: str | None) -> str:
    resolved = resolve_visual_style(style)
    if not resolved:
        return ""
    if image_style_dna(resolved) == image_style_dna(DEFAULT_VISUAL_STYLE):
        return DEFAULT_VISUAL_STYLE
    return resolved


def _dialogue_notation(dialogues: list[Dialogue]) -> str:
    items: list[str] = []
    for dialogue in dialogues:
        speaker = dialogue.speaker_name or (
            dialogue.character.name if dialogue.character else "Character"
        )
        emotion = str(dialogue.emotion or "").strip()
        emotion_suffix = f"（{emotion}）" if emotion else ""
        items.append(
            f"{speaker}{emotion_suffix}说：{{{_dialogue_text(dialogue.text)}}}"
        )
    return "；".join(items)


def _unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _dialogue_text(value: str) -> str:
    return str(value or "").replace("{", "").replace("}", "").strip()


def _sound_notation(values: list[str]) -> str:
    return " ".join(f"<{item.strip('<> ')}>" for item in values if item.strip("<> "))


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, dict):
            value = "，".join(
                str(item).strip()
                for item in value.values()
                if str(item).strip()
            )
        elif isinstance(value, list):
            value = "，".join(str(item).strip() for item in value if str(item).strip())
        text = str(value).strip() if value is not None else ""
        if text:
            return text
    return ""


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
