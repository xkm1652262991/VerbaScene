from __future__ import annotations

import re


MUTABLE_PROP_STATE_TOKENS = (
    "受伤",
    "下垂",
    "无法展开",
    "流血",
    "康复",
    "治愈",
    "打开",
    "关闭",
    "发光",
    "熄灭",
    "破损",
)

_MUTABLE_NAME_PREFIXES = ("受伤的", "受伤", "康复的", "康复", "治愈的", "治愈", "破损的", "破损")
_BIRD_RECOVERY_CUES = ("恢复", "康复", "痊愈", "振翅", "起飞", "腾空", "飞向", "飞离", "鸣唱")


def contains_mutable_prop_state(*values: object) -> bool:
    return any(_clause_contains_mutable_state(clause) for clause in _clauses(*values))


def immutable_prop_name(name: object) -> str:
    value = str(name or "").strip()
    for prefix in _MUTABLE_NAME_PREFIXES:
        if value.startswith(prefix) and len(value) > len(prefix):
            return value[len(prefix) :].strip()
    return value or "道具"


def immutable_prop_identity(*values: object) -> str:
    """从道具描述中保留可跨镜头复用的外观，剔除伤势、开关等瞬时状态短语。"""
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        clauses = _clauses(text)
        stable = [
            clause
            for clause in clauses
            if not _clause_contains_mutable_state(clause)
        ]
        if stable:
            return "，".join(stable)
    return immutable_prop_name(values[-1] if values else "")


def mutable_prop_state(*values: object) -> str:
    for value in values:
        states = [clause for clause in _clauses(value) if _clause_contains_mutable_state(clause)]
        if states:
            return "，".join(states)
    return ""


def prop_state_for_visible_moment(
    *,
    name: object,
    visual_prompt: object,
    description: object,
    visible_context: object,
) -> str:
    state = mutable_prop_state(visual_prompt, description)
    if not state:
        return ""
    stable_name = immutable_prop_name(name)
    context = str(visible_context or "")
    if "鸟" in stable_name and any(cue in context for cue in _BIRD_RECOVERY_CUES):
        return f"{stable_name}：已康复，双翼对称且可正常展开；具体姿态服从唯一可见瞬间"
    return f"{stable_name}：{state}"


def _clauses(*values: object) -> list[str]:
    text = "，".join(str(value or "").strip() for value in values if str(value or "").strip())
    return [part.strip() for part in re.split(r"[，,；;。]", text) if part.strip()]


def _clause_contains_mutable_state(clause: str) -> bool:
    for token in MUTABLE_PROP_STATE_TOKENS:
        start = clause.find(token)
        while start >= 0:
            prefix = clause[max(0, start - 3) : start]
            if not any(negation in prefix for negation in ("无", "未", "不", "没有")):
                return True
            start = clause.find(token, start + len(token))
    return False
