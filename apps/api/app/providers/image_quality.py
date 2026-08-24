from __future__ import annotations

from typing import Any


IMAGE_QUALITY_STEPS = {
    "low": 20,
    "medium": 35,
    "high": 50,
    "auto": 50,
}


def image_quality(params: dict[str, Any], default_quality: str) -> str:
    quality = str(params.get("quality", default_quality)).strip().lower()
    if quality not in IMAGE_QUALITY_STEPS:
        raise ValueError(f"Unsupported image quality: {quality!r}")
    return quality


def explicit_image_steps(params: dict[str, Any], default_steps: int | None) -> int | None:
    value = params.get("steps")
    if value is None:
        value = params.get("num_inference_steps")
    if value is None:
        value = default_steps
    if value is None:
        return None
    steps = int(value)
    if not 1 <= steps <= 100:
        raise ValueError("Image steps must be between 1 and 100")
    return steps


def resolved_image_steps(
    params: dict[str, Any],
    *,
    default_steps: int | None,
    default_quality: str,
) -> int:
    steps = explicit_image_steps(params, default_steps)
    if steps is not None:
        return steps
    return IMAGE_QUALITY_STEPS[image_quality(params, default_quality)]
