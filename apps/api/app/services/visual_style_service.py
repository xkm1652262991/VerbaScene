"""Project visual-style rebasing without rewriting story or media history."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.style import strip_project_style
from app.models import Character, Prop, Scene, Shot


def rebase_stored_visual_prompts(
    db: Session,
    project_id: str,
    *,
    previous_style: str | None,
) -> dict[str, int]:
    """Remove the previous project style from stored subject prompts.

    Generated Asset and AssetCandidate records are intentionally untouched:
    their request prompts describe what was actually generated and remain
    immutable provenance. Current entity/shot source prompts become
    style-neutral and video prompts are marked stale for explicit recompilation.
    """

    counts = {"characters": 0, "scenes": 0, "props": 0, "shots": 0}

    for character in db.scalars(select(Character).where(Character.project_id == project_id)):
        changed = False
        character.asset_spec, prompt_changed = _strip_prompt_map(character.asset_spec, previous_style)
        changed = changed or prompt_changed
        neutral = strip_project_style(character.fixed_prompt, previous_style)
        if neutral != (character.fixed_prompt or ""):
            character.fixed_prompt = neutral or None
            changed = True
        if changed:
            db.add(character)
            counts["characters"] += 1

    for scene in db.scalars(select(Scene).where(Scene.project_id == project_id)):
        changed = False
        scene.asset_spec, prompt_changed = _strip_prompt_map(scene.asset_spec, previous_style)
        changed = changed or prompt_changed
        neutral = strip_project_style(scene.fixed_prompt, previous_style)
        if neutral != (scene.fixed_prompt or ""):
            scene.fixed_prompt = neutral or None
            changed = True
        if changed:
            db.add(scene)
            counts["scenes"] += 1

    for prop in db.scalars(select(Prop).where(Prop.project_id == project_id)):
        changed = False
        prop.asset_spec, prompt_changed = _strip_prompt_map(prop.asset_spec, previous_style)
        changed = changed or prompt_changed
        neutral = strip_project_style(prop.visual_prompt, previous_style)
        if neutral != (prop.visual_prompt or ""):
            prop.visual_prompt = neutral or None
            changed = True
        if changed:
            db.add(prop)
            counts["props"] += 1

    for shot in db.scalars(select(Shot).where(Shot.project_id == project_id)):
        changed = False
        neutral = strip_project_style(shot.image_prompt, previous_style)
        if neutral != (shot.image_prompt or ""):
            shot.image_prompt = neutral or None
            changed = True

        card = deepcopy(shot.shot_card) if isinstance(shot.shot_card, dict) else {}
        prompts = card.get("image_prompts")
        if isinstance(prompts, dict):
            next_prompts = deepcopy(prompts)
            for item in next_prompts.values():
                if not isinstance(item, dict):
                    continue
                positive = item.get("positive_prompt")
                stripped = strip_project_style(
                    positive if isinstance(positive, str) else None,
                    previous_style,
                )
                if isinstance(positive, str) and stripped != positive:
                    item["positive_prompt"] = stripped
                    changed = True
            card["image_prompts"] = next_prompts

        if shot.video_prompt:
            card["prompt_stale"] = True
            card["stale_reason"] = "项目视觉风格已更新，请重新编译视频 Prompt"
            changed = True
        if changed:
            shot.shot_card = card
            db.add(shot)
            counts["shots"] += 1

    db.flush()
    return counts


def _strip_prompt_map(value: Any, style: str | None) -> tuple[dict[str, Any], bool]:
    spec = deepcopy(value) if isinstance(value, dict) else {}
    prompts = spec.get("image_prompts")
    if not isinstance(prompts, dict):
        return spec, False

    changed = False
    next_prompts = deepcopy(prompts)
    for item in next_prompts.values():
        if not isinstance(item, dict):
            continue
        positive = item.get("positive_prompt")
        stripped = strip_project_style(
            positive if isinstance(positive, str) else None,
            style,
        )
        if isinstance(positive, str) and stripped != positive:
            item["positive_prompt"] = stripped
            changed = True
    spec["image_prompts"] = next_prompts
    return spec, changed
