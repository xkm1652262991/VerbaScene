import re
from typing import Any


UNSPECIFIED_STYLE_VALUES = {"", "default", "none", "null", "cyber_suspense"}

DEFAULT_VISUAL_STYLE = "高品质风格化三维儿童动画"

GENERIC_ANIMATION_STYLE_VALUES = {
    "动画",
    "动画风格",
    "animation",
    "animation style",
}

DEFAULT_VISUAL_STYLE_VALUES = {
    DEFAULT_VISUAL_STYLE.lower(),
    "风格化三维儿童动画",
    "三维儿童动画",
    "3d儿童动画",
    "stylized 3d animation",
    "stylized 3d children animation",
}

DEFAULT_VISUAL_STYLE_DNA = (
    "高品质风格化三维儿童动画电影质感，圆润可爱的风格化角色造型，"
    "清晰的三维体积和空间纵深，按对象正确表现细腻可辨识的皮毛、皮肤、布料、木材与环境材质，"
    "电影级柔和全局照明、自然接触阴影和适度景深，色彩明亮温暖，"
    "表情生动克制、动作清楚易读，角色比例、材质、光照与场景空间跨镜头保持一致"
)

# Compatibility alias for older internal imports. New code should use the
# explicitly named DEFAULT_VISUAL_STYLE_DNA.
ANIMATION_STYLE_DNA = DEFAULT_VISUAL_STYLE_DNA


def clean_project_style(style: str | None) -> str:
    value = (style or "").strip()
    if value.lower() in UNSPECIFIED_STYLE_VALUES:
        return ""
    return value


def resolve_visual_style(
    style: str | None,
    creative_settings: dict[str, Any] | None = None,
    *,
    use_default: bool = True,
) -> str:
    """Resolve the single project visual style without mixing prompt copies.

    Project.style has precedence. creative_settings.animation_style is only a
    compatibility mirror for old payloads and projects.
    """

    value = clean_project_style(style)
    if not value and isinstance(creative_settings, dict):
        value = clean_project_style(str(creative_settings.get("animation_style") or ""))
    if value:
        return value
    return DEFAULT_VISUAL_STYLE if use_default else ""


def image_style_dna(style: str | None) -> str:
    """Compile a visible project style label into executable media rules."""

    value = clean_project_style(style)
    if not value:
        return ""
    if value.lower() in GENERIC_ANIMATION_STYLE_VALUES | DEFAULT_VISUAL_STYLE_VALUES:
        return DEFAULT_VISUAL_STYLE_DNA
    return value


def project_style_prompt_line(style: str | None) -> str:
    value = image_style_dna(resolve_visual_style(style))
    return f"项目视觉风格（只用于设计兼容性，不要复制进结构化主体 Prompt）：{value}"


def style_phrase(style: str | None) -> str:
    value = image_style_dna(style)
    return f"{value}，" if value else ""


def compose_image_prompt(prompt: str | None, style: str | None) -> str:
    """Attach the current project style at the Provider boundary."""

    subject = (prompt or "").strip()
    visual_style = image_style_dna(resolve_visual_style(style))
    if not subject:
        return ""
    if visual_style in subject:
        return subject
    return f"视觉风格：{visual_style}。\n画面内容：{subject}"


def strip_project_style(prompt: str | None, style: str | None) -> str:
    """Remove a copied project style from a stored subject prompt.

    This is deliberately limited to the exact previous style and its compiled
    DNA. It must not erase an explicitly authored medium choice unrelated to
    the project value.
    """

    value = (prompt or "").strip()
    raw_style = clean_project_style(style)
    if not value or not raw_style:
        return value

    fragments = {raw_style, image_style_dna(raw_style)}
    if "动画，" in raw_style:
        fragments.add(raw_style.replace("动画，", "动画风格，", 1))
    if "动画风格，" in raw_style:
        fragments.add(raw_style.replace("动画风格，", "动画，", 1))
    if raw_style.endswith("动画"):
        fragments.add(f"{raw_style}风格")

    ordered_fragments = sorted((item for item in fragments if item), key=len, reverse=True)
    style_was_at_end = any(
        re.search(rf"{re.escape(fragment)}\s*[。；，,]*$", value)
        for fragment in ordered_fragments
    )
    for fragment in ordered_fragments:
        value = value.replace(fragment, "")

    value = re.sub(r"视觉风格\s*[：:]\s*(?=[。；，,]|$)", "", value)
    value = re.sub(r"画面内容\s*[：:]\s*", "", value)
    value = re.sub(r"^[\s。；，,]+", "", value)
    value = re.sub(r"[；，,]\s*[；，,]+", "；", value)
    value = re.sub(r"。\s*。+", "。", value)
    value = re.sub(r"[；，,]\s*。", "。", value)
    if style_was_at_end:
        value = re.sub(r"[。；，,\s]+$", "", value)
    return value.strip()
