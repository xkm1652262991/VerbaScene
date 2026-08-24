import re


SHOT_SIZE_LABELS = {
    "extreme_wide": "大全景",
    "establishing": "建立镜头",
    "wide": "全景",
    "full_shot": "全身景",
    "medium_wide": "中远景",
    "medium": "中景",
    "medium_close_up": "中近景",
    "close_up": "特写",
    "extreme_close_up": "大特写",
    "insert": "插入特写",
}

ANGLE_LABELS = {
    "eye_level": "平视机位",
    "slight_low_angle": "轻微低角度",
    "low_angle": "低角度仰拍",
    "high_angle": "高角度俯拍",
    "overhead": "顶拍",
    "dutch_angle": "倾斜构图",
    "profile": "侧面机位",
    "over_shoulder": "过肩机位",
    "slight_wide_angle": "轻微广角机位",
    "wide_angle": "广角机位",
}

MOVEMENT_LABELS = {
    "static": "固定机位",
    "locked": "固定机位",
    "stable": "稳定镜头",
    "dolly_in": "缓慢推进",
    "push_in": "缓慢推进",
    "slow_push_in": "缓慢推进",
    "dolly_out": "缓慢后拉",
    "pull_back": "缓慢后拉",
    "pan_left": "向左摇移",
    "pan_right": "向右摇移",
    "tilt_up": "向上摇镜",
    "tilt_down": "向下摇镜",
    "tracking": "跟拍",
    "follow": "跟拍",
    "handheld": "轻微手持晃动",
    "handheld_shake": "轻微手持晃动",
}


def professional_shot_size(value: object, fallback: str = "中景") -> str:
    text = _clean(value)
    if not text:
        return fallback
    if _has_cjk(text):
        return text
    normalized = _normalize_key(text)
    if normalized in SHOT_SIZE_LABELS:
        return SHOT_SIZE_LABELS[normalized]
    if "extreme" in normalized and "close" in normalized:
        return "大特写"
    if "close" in normalized:
        return "特写"
    if "medium" in normalized and "close" in normalized:
        return "中近景"
    if "medium" in normalized and "wide" in normalized:
        return "中远景"
    if "medium" in normalized:
        return "中景"
    if "wide" in normalized or "establish" in normalized:
        return "全景"
    return text


def professional_angle(value: object, fallback: str = "") -> str:
    text = _clean(value)
    if not text:
        return fallback
    if _has_cjk(text):
        return text
    normalized = _normalize_key(text)
    if normalized in ANGLE_LABELS:
        return ANGLE_LABELS[normalized]
    if "low" in normalized:
        return "低角度仰拍"
    if "high" in normalized:
        return "高角度俯拍"
    if "eye" in normalized:
        return "平视机位"
    if "wide" in normalized:
        return "广角机位"
    return text


def professional_movement(value: object, fallback: str = "固定机位") -> str:
    text = _clean(value)
    if not text:
        return fallback
    if _has_cjk(text):
        return text
    normalized = _normalize_key(text)
    if normalized in MOVEMENT_LABELS:
        return MOVEMENT_LABELS[normalized]
    if any(key in normalized for key in ("dolly_in", "push_in", "zoom_in")):
        return "缓慢推进"
    if any(key in normalized for key in ("dolly_out", "pull_back", "zoom_out")):
        return "缓慢后拉"
    if "pan" in normalized and "right" in normalized:
        return "向右摇移"
    if "pan" in normalized and "left" in normalized:
        return "向左摇移"
    if "pan" in normalized:
        return "水平摇移"
    if "handheld" in normalized or "shake" in normalized:
        return "轻微手持晃动"
    if "track" in normalized or "follow" in normalized:
        return "跟拍"
    if "static" in normalized or "locked" in normalized:
        return "固定机位"
    return text


def professional_camera_summary(
    *,
    shot_size: object,
    angle: object = "",
    movement: object,
) -> str:
    parts = [
        professional_shot_size(shot_size),
        professional_angle(angle),
        professional_movement(movement),
    ]
    return "，".join(part for part in parts if part)


def _clean(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _normalize_key(text: str) -> str:
    normalized = text.strip().lower()
    normalized = normalized.replace("-", "_").replace(" ", "_")
    normalized = re.sub(r"[^a-z0-9_]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized
