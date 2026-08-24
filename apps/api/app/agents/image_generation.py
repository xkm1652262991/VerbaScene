from typing import Any

from app.agents.style import compose_image_prompt
from app.models import Character, Prop, Scene, Shot


DEFAULT_IMAGE_NEGATIVE_PROMPT = "字幕，水印，logo，可读文字"


def character_reference_prompt(
    character: Character,
    style: str = "",
    asset_role: str = "character_main_ref",
) -> str:
    prompt = _stored_reference_prompt(character, asset_role) or character.fixed_prompt or character.appearance or character.name
    return compose_image_prompt(prompt, style)


def scene_reference_prompt(scene: Scene, style: str = "") -> str:
    prompt = _stored_reference_prompt(scene, "scene_ref") or scene.fixed_prompt or scene.description or scene.name
    return compose_image_prompt(prompt, style)


def prop_reference_prompt(prop: Prop, style: str = "") -> str:
    prompt = _stored_reference_prompt(prop, "prop_ref") or prop.visual_prompt or prop.description or prop.name
    return compose_image_prompt(prompt, style)


def reference_negative_prompt(entity: Character | Scene | Prop, asset_role: str) -> str:
    prompts = _stored_prompt_map(entity)
    item = prompts.get(asset_role)
    if isinstance(item, dict):
        value = item.get("negative_prompt")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return DEFAULT_IMAGE_NEGATIVE_PROMPT


def shot_image_prompt(
    shot: Shot,
    *,
    characters: list[Character] | None = None,
    scene: Scene | None = None,
    props: list[Prop] | None = None,
    style: str | None = None,
) -> str:
    _ = (characters, scene, props)
    return compose_image_prompt(shot.image_prompt, style)


def shot_negative_prompt(
    shot: Shot,
    *,
    characters: list[Character] | None = None,
    scene: Scene | None = None,
    props: list[Prop] | None = None,
    style: str | None = None,
) -> str | None:
    _ = (characters, scene, props, style)
    value = (shot.negative_prompt or "").strip()
    return value or None


def _stored_reference_prompt(entity: Character | Scene | Prop, asset_role: str) -> str | None:
    item = _stored_prompt_map(entity).get(asset_role)
    if not isinstance(item, dict):
        return None
    value = item.get("positive_prompt")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _stored_prompt_map(entity: Character | Scene | Prop) -> dict[str, Any]:
    asset_spec = entity.asset_spec if isinstance(entity.asset_spec, dict) else {}
    prompts = asset_spec.get("image_prompts")
    return prompts if isinstance(prompts, dict) else {}
