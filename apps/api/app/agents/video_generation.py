from decimal import Decimal, InvalidOperation
from typing import Any

from app.models import Character, Prop, Scene, Shot
from app.agents.prompt_engineering import build_shot_video_prompt
from app.agents.shot_timing import strip_internal_timing


WAN_I2V_PROMPT_VERSION = "native-audio-video-prompt-v1"


def shot_video_prompt(
    shot: Shot,
    *,
    characters: list[Character] | None = None,
    scene: Scene | None = None,
    props: list[Prop] | None = None,
) -> str:
    if isinstance(shot.video_prompt, str) and shot.video_prompt.strip():
        return strip_internal_timing(shot.video_prompt)
    card = shot.shot_card if isinstance(shot.shot_card, dict) else {}
    project = getattr(shot, "project", None)
    return build_shot_video_prompt(
        description=shot.description,
        camera_shot=shot.camera_shot,
        camera_movement=shot.camera_movement,
        characters=characters or [],
        scene=scene,
        props=props or [],
        dialogues=list(getattr(shot, "dialogues", []) or []),
        shot_card=card,
        aspect_ratio=getattr(project, "aspect_ratio", "16:9"),
        resolution=getattr(project, "resolution", "854x480"),
        animation_style=(
            getattr(project, "style", None)
            or (getattr(project, "creative_settings", {}) or {}).get("animation_style", "")
            if project is not None
            else ""
        ),
    )


def shot_video_params(
    shot: Shot,
    *,
    fps: int = 16,
    max_frames: int = 81,
    default_steps: int = 40,
    default_guidance: float = 3.5,
) -> dict[str, Any]:
    card = shot.shot_card if isinstance(shot.shot_card, dict) else {}
    risks = _string_list(card.get("risk_flags"))
    duration = _duration_seconds(shot.duration_sec)
    frames = _frames_for_duration(duration, fps=fps, max_frames=max_frames)

    steps = min(default_steps, 32)
    guidance = default_guidance
    if any(risk in risks for risk in ("fast_motion", "complex_motion", "spatial_translation", "object_manipulation")):
        steps = min(default_steps, 36)
        guidance = min(default_guidance, 3.3)
    elif "low_light" in risks or "multi_character" in risks:
        steps = min(default_steps, 34)
    elif _is_stable_camera(shot.camera_movement):
        steps = min(default_steps, 28)

    return {
        "duration_sec": duration,
        "frames": frames,
        # Some distilled runtimes (including the current LightX2V service)
        # intentionally require four inference steps. Respect the configured
        # provider contract instead of imposing the legacy 16-step floor.
        "steps": max(1, steps),
        "guidance": guidance,
        "fps": fps,
        "i2v_prompt_version": WAN_I2V_PROMPT_VERSION,
        "i2v_risk_flags": risks,
    }


def _duration_seconds(value: object) -> float:
    try:
        duration = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        duration = Decimal("3")
    duration = max(Decimal("1"), min(duration, Decimal("30")))
    return float(duration)


def _frames_for_duration(duration_sec: float, *, fps: int, max_frames: int) -> int:
    target = max(1, round(duration_sec * fps))
    frames = ((target - 1) // 4) * 4 + 1
    if frames < target:
        frames += 4
    return max(17, min(frames, max_frames))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _is_stable_camera(value: str | None) -> bool:
    text = (value or "").lower()
    return any(key in text for key in ("稳定", "固定", "static", "locked", "stable"))
