from __future__ import annotations

import base64
import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from PIL import Image, ImageOps, ImageStat, UnidentifiedImageError

from app.core.config import settings


@dataclass(frozen=True)
class ImageSignature:
    uri: str
    width: int
    height: int
    average_rgb: tuple[float, float, float]
    brightness: float
    warm_ratio: float
    orange_ratio: float
    ahash: int


def evaluate_image_consistency(
    *,
    image_uri: str,
    asset_resolution: dict[str, Any] | None,
) -> dict[str, Any]:
    reference_assets = _reference_assets(asset_resolution)
    prompt_context = _prompt_context(asset_resolution)
    issues: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    try:
        candidate = _signature(image_uri)
    except (ValueError, OSError, HTTPError, URLError, TimeoutError, UnidentifiedImageError) as exc:
        return {
            "version": "image-consistency-v1",
            "status": "warning",
            "score": 60,
            "reference_count": len(reference_assets),
            "checks": [],
            "issues": [_issue("warning", "candidate_image_unreadable", str(exc)[:240])],
        }

    _aspect_check(candidate, checks, issues)

    readable_refs = []
    for reference in reference_assets[:8]:
        uri = str(reference.get("uri") or "")
        if not uri:
            continue
        try:
            readable_refs.append((reference, _signature(uri)))
        except (ValueError, OSError, HTTPError, URLError, TimeoutError, UnidentifiedImageError) as exc:
            issues.append(
                _issue(
                    "warning",
                    "reference_image_unreadable",
                    f"{reference.get('reference_role') or reference.get('asset_role') or 'reference'} 无法读取：{str(exc)[:160]}",
                )
            )

    if reference_assets and not readable_refs:
        issues.append(_issue("warning", "no_readable_references", "存在参考资产，但当前进程无法读取参考图内容"))
    elif not reference_assets and _expects_references(prompt_context):
        issues.append(_issue("warning", "missing_reference_assets", "该镜头有角色/场景/道具约束，但没有解析到参考图"))

    _provider_reference_capability_check(asset_resolution, reference_assets, checks, issues)
    _reference_similarity_checks(candidate, readable_refs, checks, issues)
    _orange_cat_check(candidate, readable_refs, prompt_context, checks, issues)

    score = _score(issues)
    return {
        "version": "image-consistency-v1",
        "status": _status(issues),
        "score": score,
        "reference_count": len(reference_assets),
        "readable_reference_count": len(readable_refs),
        "checks": checks,
        "issues": issues,
        "metrics": {
            "width": candidate.width,
            "height": candidate.height,
            "average_rgb": [round(value, 2) for value in candidate.average_rgb],
            "brightness": round(candidate.brightness, 3),
            "warm_ratio": round(candidate.warm_ratio, 4),
            "orange_ratio": round(candidate.orange_ratio, 4),
        },
    }


def _reference_assets(asset_resolution: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(asset_resolution, dict):
        return []
    assets = asset_resolution.get("reference_assets")
    return [item for item in assets if isinstance(item, dict)] if isinstance(assets, list) else []


def _prompt_context(asset_resolution: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(asset_resolution, dict):
        return {}
    context = asset_resolution.get("prompt_context")
    return context if isinstance(context, dict) else {}


def _signature(uri: str) -> ImageSignature:
    image = _load_image(uri)
    rgb = ImageOps.exif_transpose(image).convert("RGB")
    small = rgb.resize((32, 32), Image.Resampling.LANCZOS)
    stat = ImageStat.Stat(small)
    avg = tuple(float(value) for value in stat.mean[:3])
    pixels = list(small.getdata())
    total = max(1, len(pixels))
    warm = sum(1 for red, green, blue in pixels if red > blue + 18 and green >= blue) / total
    orange = sum(
        1
        for red, green, blue in pixels
        if red >= 120 and 55 <= green <= 205 and blue <= 155 and red > green * 1.04 and green > blue * 1.05
    ) / total
    return ImageSignature(
        uri=uri,
        width=rgb.width,
        height=rgb.height,
        average_rgb=avg,
        brightness=sum(avg) / 3,
        warm_ratio=warm,
        orange_ratio=orange,
        ahash=_average_hash(rgb),
    )


def _load_image(uri: str) -> Image.Image:
    if uri.startswith("data:image/") and ";base64," in uri:
        _, encoded = uri.split(",", 1)
        return Image.open(BytesIO(base64.b64decode(encoded)))
    local_path = _local_storage_uri_path(uri)
    if local_path is not None:
        return Image.open(local_path)
    if uri.startswith(("http://", "https://")):
        with urlopen(uri, timeout=settings.provider_timeout_sec) as response:
            return Image.open(BytesIO(response.read()))
    raise ValueError(f"Unsupported image URI: {uri[:48]}")


def _local_storage_uri_path(uri: str) -> Path | None:
    public_base_url = settings.public_storage_base_url.rstrip("/")
    if not public_base_url or not uri.startswith(f"{public_base_url}/"):
        return None
    relative = uri.removeprefix(f"{public_base_url}/").lstrip("/")
    path = (Path(settings.storage_root) / relative).resolve()
    storage_root = Path(settings.storage_root).resolve()
    if storage_root not in (path, *path.parents) or not path.is_file():
        return None
    return path


def _average_hash(image: Image.Image) -> int:
    gray = ImageOps.grayscale(image.resize((8, 8), Image.Resampling.LANCZOS))
    values = list(gray.getdata())
    mean = sum(values) / max(1, len(values))
    result = 0
    for index, value in enumerate(values):
        if value >= mean:
            result |= 1 << index
    return result


def _aspect_check(candidate: ImageSignature, checks: list[dict[str, Any]], issues: list[dict[str, Any]]) -> None:
    ratio = candidate.width / max(candidate.height, 1)
    delta = abs(ratio - (16 / 9))
    checks.append({"name": "aspect_ratio", "value": round(ratio, 4), "target": "16:9", "delta": round(delta, 4)})
    if delta > 0.08:
        issues.append(_issue("warning", "aspect_ratio_mismatch", "图片比例偏离 16:9，可能影响后续图生视频稳定性"))


def _reference_similarity_checks(
    candidate: ImageSignature,
    readable_refs: list[tuple[dict[str, Any], ImageSignature]],
    checks: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> None:
    if not readable_refs:
        return
    scene_refs = [
        signature
        for reference, signature in readable_refs
        if reference.get("reference_role") in {"scene_ref", "shot_storyboard", "storyboard_first_frame"}
    ]
    if not scene_refs:
        return
    best = max(_style_similarity(candidate, reference) for reference in scene_refs)
    checks.append({"name": "scene_style_similarity", "score": round(best, 2), "reference_count": len(scene_refs)})
    if best < 42:
        issues.append(_issue("warning", "scene_style_drift", "图片整体色温/亮度/构图与场景或分镜参考差异较大"))


def _orange_cat_check(
    candidate: ImageSignature,
    readable_refs: list[tuple[dict[str, Any], ImageSignature]],
    prompt_context: dict[str, Any],
    checks: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> None:
    if not _mentions_orange_cat(prompt_context):
        return
    character_refs = [
        signature
        for reference, signature in readable_refs
        if reference.get("reference_role") == "character_main_ref"
    ]
    ref_orange = max((signature.orange_ratio for signature in character_refs), default=0.0)
    checks.append(
        {
            "name": "orange_cat_color_anchor",
            "candidate_orange_ratio": round(candidate.orange_ratio, 4),
            "reference_orange_ratio": round(ref_orange, 4),
        }
    )
    min_ratio = max(0.006, ref_orange * 0.08)
    if candidate.orange_ratio < min_ratio:
        issues.append(_issue("warning", "orange_cat_color_weak", "橘猫主体颜色占比偏低，可能出现花色或主体漂移"))


def _mentions_orange_cat(prompt_context: dict[str, Any]) -> bool:
    characters = prompt_context.get("characters")
    if not isinstance(characters, list):
        return False
    text = " ".join(
        " ".join(str(character.get(key) or "") for key in ("name", "fixed_prompt", "appearance", "identity"))
        for character in characters
        if isinstance(character, dict)
    )
    return any(keyword in text.lower() for keyword in ("橘猫", "橘色猫", "orange cat", "ginger cat", "orange tabby"))


def _expects_references(prompt_context: dict[str, Any]) -> bool:
    return any(prompt_context.get(key) for key in ("characters", "scene", "props"))


def _provider_reference_capability_check(
    asset_resolution: dict[str, Any] | None,
    reference_assets: list[dict[str, Any]],
    checks: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> None:
    provider = asset_resolution.get("provider") if isinstance(asset_resolution, dict) else None
    if not isinstance(provider, dict):
        return
    capabilities = {
        str(capability)
        for capability in provider.get("capabilities", [])
        if isinstance(capability, str)
    }
    identity_refs = [
        reference
        for reference in reference_assets
        if str(reference.get("reference_role") or "").startswith("character_source_ref")
        or reference.get("reference_role") == "character_main_ref"
    ]
    if not identity_refs:
        return
    supports_identity_reference = bool({"reference_to_image", "reference_payload"} & capabilities)
    checks.append(
        {
            "name": "provider_identity_reference_capability",
            "provider": provider.get("name"),
            "supports_reference_payload": supports_identity_reference,
            "identity_reference_count": len(identity_refs),
        }
    )
    if not supports_identity_reference:
        issues.append(
            _issue(
                "error",
                "provider_lacks_identity_reference",
                "该镜头依赖角色源图保真，但当前图片 Provider 不支持参考图输入；生成结果不能作为人物/小猫身份保真图通过。",
            )
        )


def _style_similarity(left: ImageSignature, right: ImageSignature) -> float:
    color_distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(left.average_rgb, right.average_rgb)))
    brightness_distance = abs(left.brightness - right.brightness)
    warm_distance = abs(left.warm_ratio - right.warm_ratio) * 100
    hash_distance = (left.ahash ^ right.ahash).bit_count() / 64 * 100
    penalty = color_distance * 0.35 + brightness_distance * 0.15 + warm_distance * 0.25 + hash_distance * 0.25
    return max(0.0, 100.0 - penalty)


def _issue(severity: str, code: str, message: str) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message}


def _score(issues: list[dict[str, Any]]) -> int:
    score = 100
    for issue in issues:
        score -= 25 if issue.get("severity") == "error" else 10
    return max(0, score)


def _status(issues: list[dict[str, Any]]) -> str:
    severities = {issue.get("severity") for issue in issues}
    if "error" in severities:
        return "fail"
    if "warning" in severities:
        return "warning"
    return "pass"
