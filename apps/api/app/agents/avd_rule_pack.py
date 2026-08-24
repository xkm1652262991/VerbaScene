from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


PromptSeverity = Literal["error", "warning"]


@dataclass(frozen=True)
class AVDPromptIssue:
    severity: PromptSeverity
    code: str
    message: str


AVD_RULE_PACK_VERSION = "avd-rule-pack-v3-semantic"

_BASE_NEGATIVE_ITEMS = [
    "低清晰度",
    "角色脸部崩坏",
    "多余手指",
    "手部畸形",
    "肢体变形",
    "字幕",
    "水印",
    "logo",
    "乱码文字",
    "可读文字",
    "画面拼贴",
    "split composition",
    "text overlay",
]

_READABLE_TEXT_PATTERNS = [
    re.compile(r"(屏幕|招牌|书页|报纸|石碑|铭文|工牌|名牌|海报|字幕).{0,16}(显示|写着|写有|刻着|内容为|出现文字)"),
    re.compile(r"(显示|写着|写有|刻着|内容为).{0,12}[「『\"']"),
    re.compile(r"\b(subtitle shows|subtitle can say|text overlay|readable text|readable letters|name tag|id card)\b", re.I),
    re.compile(r"\b(reads|showing|written|inscription|inscribed|legible)\b.{0,40}[\"']?[A-Za-z0-9]{2,}", re.I),
    re.compile(r"\b(404|500|error|success)\b", re.I),
]

_SPLIT_COMPOSITION_PATTERNS = [
    re.compile(r"\b(split composition|split screen|diptych|left half|right half)\b", re.I),
    re.compile(r"(左半|右半|左右分屏|上下分屏|双联画面|拼贴分割)"),
]

_GHOST_TONE_PATTERNS = [
    re.compile(r"\b(ghostly|spectral|phantom|floating figure|jump scare|creepy|eerie)\b", re.I),
    re.compile(r"(鬼影|幽灵|漂浮人影|跳吓|阴森恐怖)"),
]

_NARROW_SPACE_TOKENS = ("便利店", "办公室", "车内", "卧室", "电梯", "走廊", "狭小空间", "apartment", "office", "car interior")
_IMPRACTICAL_CAMERA_TOKENS = ("环绕", "航拍", "穿墙", "穿越墙面", "中轴旋转", "orbital", "drone", "fly through wall")

_STATIC_IMAGE_SEQUENCE_PATTERNS = [
    re.compile(r"(动作瞬间|可见动作).{0,80}(关键动作|收束|尾帧)", re.S),
    re.compile(r"起始.{0,80}(关键动作|主动作).{0,80}(收束|结束)", re.S),
    re.compile(r"\b(start(?:ing)? state|key action|main action).{0,100}(end state|ending)\b", re.I | re.S),
]

_STATIC_IMAGE_CAMERA_MOVE_PATTERNS = [
    re.compile(r"(缓慢)?(前推|后拉|推进|横移|跟拍|环绕|摇摄|升降镜头|推镜|拉镜)"),
    re.compile(r"\b(dolly|pan(?:ning)?|tracking shot|orbit(?:al)?|camera move(?:ment)?)\b", re.I),
]

_GENERIC_IMAGE_STYLE_PATTERNS = [
    re.compile(r"(风格(?:DNA)?|风格与光影|项目风格)：\s*(动画风格|动画|二维动画风格)(?=[，。\n]|$)", re.I),
    re.compile(r"\b(style|style dna):\s*animation(?: style)?(?=[,.\n]|$)", re.I),
]


def avd_negative_prompt(extra_items: list[str] | None = None) -> str:
    items = list(_BASE_NEGATIVE_ITEMS)
    if extra_items:
        items.extend(item for item in extra_items if item)
    return "，".join(dict.fromkeys(items))


def merge_negative_prompt(existing: str | None, extra_items: list[str] | None = None) -> str:
    items = []
    if existing:
        items.extend(part.strip() for part in re.split(r"[，,]\s*", existing) if part.strip())
    items.extend(_BASE_NEGATIVE_ITEMS)
    if extra_items:
        items.extend(item for item in extra_items if item)
    return "，".join(dict.fromkeys(items))


def scan_avd_prompt(
    text: str | None,
    *,
    prompt_type: Literal["image", "video", "general"] = "general",
) -> list[AVDPromptIssue]:
    value = text or ""
    issues: list[AVDPromptIssue] = []
    if not value.strip():
        return issues

    if _matches_without_negation(value, _READABLE_TEXT_PATTERNS):
        issues.append(
            AVDPromptIssue(
                "error",
                "avd_readable_text_leak",
                "Prompt 暗示生成可读文字、字幕、铭文、屏幕内容或编号，需改为图标、光影、色块或抽象纹理。",
            )
        )
    if _matches_without_negation(value, _SPLIT_COMPOSITION_PATTERNS):
        issues.append(
            AVDPromptIssue(
                "warning",
                "avd_split_composition",
                "Prompt 使用分屏、拼贴或左右半画面，作为视频参考图时容易破坏统一空间。",
            )
        )
    if _matches_without_negation(value, _GHOST_TONE_PATTERNS):
        issues.append(
            AVDPromptIssue(
                "warning",
                "avd_horror_tone_drift",
                "Prompt 含鬼片或跳吓倾向词，若目标不是恐怖片，建议改成自然反射、雨雾或情绪化光影。",
            )
        )
    if _has_any(value, _NARROW_SPACE_TOKENS) and _has_any(value, _IMPRACTICAL_CAMERA_TOKENS):
        issues.append(
            AVDPromptIssue(
                "warning",
                "avd_impractical_camera_in_narrow_space",
                "狭小空间中使用环绕、航拍或穿墙运镜，建议改为固定机位、缓慢前推、横移或过肩镜头。",
            )
        )
    if prompt_type == "image" and _matches_without_negation(value, _STATIC_IMAGE_SEQUENCE_PATTERNS):
        issues.append(
            AVDPromptIssue(
                "warning",
                "avd_static_image_temporal_sequence",
                "静态分镜图 Prompt 同时描述起始、主动作和收束，应改为一个唯一可见瞬间。",
            )
        )
    if prompt_type == "image" and _matches_without_negation(value, _STATIC_IMAGE_CAMERA_MOVE_PATTERNS):
        issues.append(
            AVDPromptIssue(
                "warning",
                "avd_static_image_camera_movement",
                "静态分镜图 Prompt 含运镜指令；图片阶段只保留景别和角度，运镜交给视频 Prompt。",
            )
        )
    if prompt_type == "image" and _matches_without_negation(value, _GENERIC_IMAGE_STYLE_PATTERNS):
        issues.append(
            AVDPromptIssue(
                "warning",
                "avd_generic_image_style",
                "图片 Prompt 只写了宽泛的动画风格，需要明确线条、上色、明暗和造型语言。",
            )
        )
    return _dedupe_issues(issues)


def issues_to_compile_notes(issues: list[AVDPromptIssue]) -> list[str]:
    return [f"{issue.severity}:avd:{issue.code}" for issue in issues]


def issues_to_dicts(issues: list[AVDPromptIssue]) -> list[dict[str, str]]:
    return [
        {
            "severity": issue.severity,
            "code": issue.code,
            "message": issue.message,
        }
        for issue in issues
    ]


def _matches_without_negation(text: str, patterns: list[re.Pattern[str]]) -> bool:
    for pattern in patterns:
        for match in pattern.finditer(text):
            prefix = text[max(0, match.start() - 12) : match.start()].lower()
            if any(token in prefix for token in ("no ", "not ", "without ", "禁止", "不要", "无", "不能")):
                continue
            return True
    return False


def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(token.lower() in lowered for token in tokens)


def _dedupe_issues(issues: list[AVDPromptIssue]) -> list[AVDPromptIssue]:
    seen: set[str] = set()
    unique: list[AVDPromptIssue] = []
    for issue in issues:
        if issue.code in seen:
            continue
        seen.add(issue.code)
        unique.append(issue)
    return unique
