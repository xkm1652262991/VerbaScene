"""Domain policy separating audible speakers from visible characters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Any, TypeVar


_EXPLICIT_VOICE_ONLY_MARKERS = (
    "旁白",
    "画外音",
    "画外声",
    "解说",
    "narrator",
    "narration",
    "voiceover",
    "offscreenvoice",
    "offscreennarrator",
    "storyteller",
)

_CONTEXTUAL_VOICE_ROLE_MARKERS = (
    "引导员",
    "虚拟引导",
    "系统语音",
    "系统声音",
    "系统提示",
    "播报员",
    "aiguide",
    "virtualguide",
    "systemvoice",
    "announcer",
)

_CharacterT = TypeVar("_CharacterT")


def speaker_key(value: Any) -> str:
    """Return a punctuation-insensitive key for names and speaker labels."""

    return re.sub(r"[^\w]+", "", str(value or "").casefold(), flags=re.UNICODE)


def voice_only_speaker_keys(script_scenes: Any) -> set[str]:
    """Identify dialogue speakers that must not become visual assets.

    Explicit narrator/voice-over labels are always voice-only. Generic guide or
    announcer labels are voice-only only when their name never appears in a
    scene's visible action; a visibly embodied guide therefore remains a normal
    character.
    """

    scenes = [item for item in script_scenes if isinstance(item, Mapping)] if isinstance(script_scenes, list) else []
    visual_text = "\n".join(speaker_key(scene.get("visible_action")) for scene in scenes)
    speakers: dict[str, str] = {}
    for scene in scenes:
        dialogues = scene.get("dialogues")
        if not isinstance(dialogues, list):
            continue
        for dialogue in dialogues:
            if not isinstance(dialogue, Mapping):
                continue
            name = str(dialogue.get("speaker") or dialogue.get("speaker_name") or "").strip()
            key = speaker_key(name)
            if key:
                speakers[key] = name

    result: set[str] = set()
    for key in speakers:
        if _has_marker(key, _EXPLICIT_VOICE_ONLY_MARKERS):
            result.add(key)
            continue
        if _has_marker(key, _CONTEXTUAL_VOICE_ROLE_MARKERS) and key not in visual_text:
            result.add(key)
    return result


def is_voice_only_speaker_name(
    name: Any,
    script_scenes: Any = None,
    *,
    known_voice_only_keys: set[str] | None = None,
) -> bool:
    key = speaker_key(name)
    if not key:
        return False
    if _has_marker(key, _EXPLICIT_VOICE_ONLY_MARKERS):
        return True
    voice_only_keys = (
        known_voice_only_keys
        if known_voice_only_keys is not None
        else voice_only_speaker_keys(script_scenes)
    )
    return key in voice_only_keys


def filter_visual_characters(
    characters: Iterable[_CharacterT],
    script_scenes: Any,
) -> list[_CharacterT]:
    """Remove voice-only speaker records while preserving real characters."""

    voice_only_keys = voice_only_speaker_keys(script_scenes)
    result: list[_CharacterT] = []
    for character in characters:
        names = _character_names(character)
        if any(
            is_voice_only_speaker_name(name, known_voice_only_keys=voice_only_keys)
            for name in names
        ):
            continue
        result.append(character)
    return result


def strip_voice_only_scene_characters(script_scenes: Any) -> list[dict[str, Any]]:
    """Remove voice-only speakers from the visual cast in place.

    Scene dictionaries intentionally retain their identity because the script
    pipeline writes stable dialogue IDs back through the same checkpoint graph.
    """

    if not isinstance(script_scenes, list):
        return []
    voice_only_keys = voice_only_speaker_keys(script_scenes)
    result: list[dict[str, Any]] = []
    for raw_scene in script_scenes:
        if not isinstance(raw_scene, Mapping):
            continue
        scene = raw_scene if isinstance(raw_scene, dict) else dict(raw_scene)
        characters = scene.get("characters")
        if isinstance(characters, list):
            scene["characters"] = [
                name
                for name in characters
                if not is_voice_only_speaker_name(
                    name,
                    known_voice_only_keys=voice_only_keys,
                )
            ]
        result.append(scene)
    return result


def _character_names(character: Any) -> list[str]:
    if isinstance(character, Mapping):
        name = character.get("name")
        asset_spec = character.get("asset_spec")
    else:
        name = getattr(character, "name", None)
        asset_spec = getattr(character, "asset_spec", None)
    names = [str(name).strip()] if str(name or "").strip() else []
    if isinstance(asset_spec, Mapping):
        aliases = asset_spec.get("aliases")
        if isinstance(aliases, list):
            names.extend(str(item).strip() for item in aliases if str(item).strip())
    return names


def _has_marker(key: str, markers: tuple[str, ...]) -> bool:
    return any(speaker_key(marker) in key for marker in markers)
