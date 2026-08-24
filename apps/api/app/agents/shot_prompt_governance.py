from __future__ import annotations

import re
from typing import Any, Iterable


SHOT_PROMPT_GOVERNANCE_VERSION = "shot-visible-scope-v2"


_CLOSE_SHOT_TOKENS = ("特写", "大特写", "近景", "微距", "局部")
_HAND_TOKENS = ("手", "指", "腕", "掌", "拳")
_FACE_TOKENS = ("脸", "面部", "眼", "眉", "鼻", "嘴", "唇", "头", "发", "耳", "表情")
_BODY_TOKENS = ("身体", "全身", "上身", "胸", "肩", "背", "腰", "腿", "脚", "翼", "翅", "羽")
_OUTFIT_TOKENS = ("衣", "裤", "裙", "鞋", "帽", "外套", "衬衫", "T恤", "制服", "书包", "配件")
_HUMAN_VISUAL_TOKENS = (
    "人物",
    "角色",
    "主角",
    "人影",
    "侧影",
    "手部",
    "手指",
    "指尖",
    "手腕",
    "手掌",
    "拳头",
    "脸部",
    "面部",
    "眼睛",
    "眉毛",
    "鼻子",
    "嘴唇",
    "头部",
    "头发",
    "发型",
    "耳朵",
    "手臂",
    "手肘",
    "脖子",
    "肩膀",
    "躯干",
    "双腿",
    "双足",
)
_HUMAN_FACE_VISUAL_TOKENS = (
    "脸部",
    "面部",
    "脸庞",
    "眼睛",
    "双眼",
    "眉毛",
    "鼻子",
    "嘴唇",
    "头部",
    "头发",
    "发型",
    "耳朵",
    "表情",
)
_ANIMAL_VISUAL_TOKENS = (
    "小鸟",
    "鸟",
    "猫",
    "狗",
    "马",
    "狼",
    "兽",
    "鸟喙",
    "喙",
    "喙尖",
    "爪趾",
    "羽毛",
    "左翼",
    "右翼",
    "双翼",
    "翼面",
    "翼尖",
    "翅膀",
    "翅羽",
    "飞羽",
    "胸羽",
    "尾羽",
    "鳞",
    "鱼鳍",
)
_EXPLICIT_HAND_CROP_TOKENS = (
    "手部入画",
    "手入画",
    "手部特写",
    "手指特写",
    "仅露手",
    "只显示手",
    "只显示指",
    "仅显示手",
    "仅显示指",
)
_ACCESSORY_NOUNS = (
    "书包",
    "背包",
    "眼镜",
    "耳钉",
    "耳环",
    "项链",
    "围巾",
    "帽子",
    "头饰",
    "手表",
    "手链",
    "戒指",
    "徽章",
    "腰带",
)
_WORKFLOW_CLAUSES = (
    "稳定身份、体型比例、配色、默认服装和标志性特征，多镜头保持一致",
    "稳定身份、体型比例、配色和标志性特征，多镜头保持一致",
    "固定空间结构、地标位置、材质和色板，不写入临时时间、天气与光线状态",
    "固定形状、尺寸比例、材质、配色和标志细节，多镜头保持一致",
    "多镜头保持一致",
)


def select_visible_characters(characters: Iterable[Any], *, visible_context: str) -> list[Any]:
    """Return only bound characters evidenced in this one static state.

    Binding means an entity is available to the shot; it does not mean the
    entity must appear in every frame.  Names/aliases are authoritative.  A
    generic body-part inference is used only when one bound entity of that
    kind can satisfy it, so an unnamed hand cannot expand into every bound
    human character.
    """

    items = list(characters)
    selected: list[Any] = []
    unresolved: list[Any] = []
    for character in items:
        name = _text(getattr(character, "name", None))
        spec = _mapping(getattr(character, "asset_spec", None))
        aliases = _string_list(spec.get("aliases"))
        if any(token and token.lower() in (visible_context or "").lower() for token in (name, *aliases)):
            selected.append(character)
        else:
            unresolved.append(character)

    for kind, anchors in (("human", _HUMAN_VISUAL_TOKENS), ("animal", _ANIMAL_VISUAL_TOKENS)):
        if not _contains_any(visible_context, anchors):
            continue
        candidates = [item for item in unresolved if _character_kind(item) == kind]
        if len(candidates) == 1:
            selected.append(candidates[0])
            unresolved.remove(candidates[0])

    return [item for item in items if item in selected]


def select_visible_props(props: Iterable[Any], *, visible_context: str) -> list[Any]:
    visible: list[Any] = []
    for prop in props:
        name = _text(getattr(prop, "name", None))
        spec = _mapping(getattr(prop, "asset_spec", None))
        aliases = _string_list(spec.get("aliases"))
        if any(token and token.lower() in (visible_context or "").lower() for token in (name, *aliases)):
            visible.append(prop)
    return visible


def hidden_entity_exclusion(label: str, all_entities: Iterable[Any], visible_entities: Iterable[Any]) -> str:
    visible_ids = {id(item) for item in visible_entities}
    hidden = [
        _text(getattr(item, "name", None))
        for item in all_entities
        if id(item) not in visible_ids and _text(getattr(item, "name", None))
    ]
    return f"当前静态瞬间未出现的绑定{label}不入画：{'、'.join(hidden)}" if hidden else ""


def governed_asset_state_text(value: str | None, *, visible_context: str) -> str:
    """Keep a compact persistent state, never a second competing pose plan."""

    clauses = _clauses(_text(value))
    if not clauses:
        return ""
    persistent_tokens = (
        "受伤",
        "伤口",
        "包扎",
        "绷带",
        "生病",
        "康复",
        "痊愈",
        "湿透",
        "弄脏",
        "破损",
        "损坏",
        "烧焦",
    )
    if _contains_any(clauses[0], persistent_tokens):
        return _join_unique_clauses(clauses)
    supported = [clause for clause in clauses if _clause_is_explicitly_supported(clause, visible_context)]
    return _join_unique_clauses(supported)


def visible_character_identity(
    character: Any,
    *,
    visible_context: str,
    shot_size: str,
    peer_names: Iterable[str] = (),
) -> tuple[str, list[str]]:
    """Compile only identity details that can affect the current frame.

    Reference images remain the full identity source. Text is deliberately a
    small, shot-local delta so an off-frame outfit or accessory cannot pull a
    close-up back toward a full-body asset-sheet composition.
    """

    name = _text(getattr(character, "name", None)) or "角色"
    spec = _mapping(getattr(character, "asset_spec", None))
    aliases = _string_list(spec.get("aliases"))
    local_context = _entity_local_context(
        visible_context,
        names=[name, *aliases],
        peer_names=[item for item in peer_names if item and item != name],
    )
    context = local_context or visible_context
    entity_kind = _text(spec.get("entity_kind")).lower()
    is_human = entity_kind in {"human", "person", "人物", "人类"} or (
        not entity_kind and _contains_any(_text(spec.get("species")), ("人", "human"))
    )
    close_shot = _is_close_shot(shot_size)
    has_visible_face = (
        _contains_any(local_context, _FACE_TOKENS)
        if local_context
        else _contains_any(context, _HUMAN_FACE_VISUAL_TOKENS)
    )
    hand_only = (
        is_human
        and _contains_any(context, _HAND_TOKENS)
        and not has_visible_face
        and (close_shot or _contains_any(context, _EXPLICIT_HAND_CROP_TOKENS))
    )
    face_detail = close_shot and (has_visible_face or (not is_human and _contains_any(context, _FACE_TOKENS)))

    if hand_only:
        return (
            f"{name}：仅显示镜头写明的手部或手指，肤色与手部造型遵循角色参考图；"
            "脸、发型、服装和配件均在画外",
            [f"{name}的脸、身体、服装和配件不得入画"],
        )

    parts: list[str] = []
    if spec:
        species = _text(spec.get("species"))
        body_type = _text(spec.get("body_type"))
        facial = _text(spec.get("facial_features"))
        surface = _text(spec.get("hair_or_surface"))
        outfit = _text(spec.get("default_outfit"))
        signatures = _string_list(spec.get("signature_features"))
        accessories = _string_list(spec.get("default_accessories"))

        _append(parts, species)
        if not close_shot or _contains_any(context, _BODY_TOKENS):
            _append(parts, body_type)
        if face_detail or not close_shot or _contains_any(context, ("眼", "脸", "头", "喙", "眉")):
            _append(parts, facial)
        if face_detail or not close_shot or _contains_any(context, ("发", "羽", "毛", "皮肤", "表面", "背")):
            _append(parts, surface)
        visible_outfit = _strip_unmentioned_accessories(outfit, accessories, context)
        if not close_shot or _text_is_mentioned(visible_outfit, context):
            _append(parts, visible_outfit)
        for item in signatures:
            visible_signature = _strip_unmentioned_accessories(item, accessories, context)
            if not close_shot or _text_is_mentioned(visible_signature, context):
                _append(parts, visible_signature)
        for item in accessories:
            if _text_is_mentioned(item, context) and not any(
                _normalized_chars(item) in _normalized_chars(part)
                for part in parts
            ):
                _append(parts, item)
    else:
        fallback = _clean_identity_text(
            _first_text(
                getattr(character, "fixed_prompt", None),
                getattr(character, "appearance", None),
                getattr(character, "identity", None),
            )
        )
        clauses = _clauses(fallback)
        if face_detail:
            clauses = [item for item in clauses if _contains_any(item, _FACE_TOKENS)]
        elif close_shot:
            clauses = [item for item in clauses if _text_is_mentioned(item, context)]
        parts.extend(clauses)

    compact = _join_unique_clauses(parts)
    if not compact:
        compact = "使用角色参考图锁定当前可见部位的身份与配色"
    return f"{name}：{compact}", []


def visible_scene_identity(scene: Any | None, *, visible_context: str, shot_size: str) -> tuple[str, list[str]]:
    if scene is None:
        return "未绑定场景；只按镜头可见内容构建简洁背景", []

    name = _text(getattr(scene, "name", None)) or "场景"
    spec = _mapping(getattr(scene, "asset_spec", None))
    close_shot = _is_close_shot(shot_size)
    exclusions: list[str] = []

    if spec:
        spatial_layout = _text(spec.get("spatial_layout"))
        candidates = [
            *_string_list(spec.get("fixed_landmarks")),
            *_string_list(spec.get("zones")),
            *_string_list(spec.get("materials")),
        ]
        ranked = _rank_relevant(candidates, visible_context)
        if close_shot:
            selected = [item for score, item in ranked if score >= 5]
            light = _extract_light_clause(spatial_layout)
            _append(selected, light)
            detail = _join_unique_clauses(selected)
            if detail:
                text = f"{name}：仅显示与镜头有关的局部——{detail}；其余空间虚化或画外"
            else:
                text = f"{name}：场景参考图只锁定当前局部的光色与材质，其余空间虚化或画外"
            exclusions.append("不得为了复现完整场景设定而补入画外地标或远景")
            return text, exclusions

        selected = [item for score, item in ranked if score >= 5]
        selected = [
            *[
                clause
                for clause in _clauses(spatial_layout)
                if _relevance_score(clause, visible_context) >= 5
            ],
            *selected,
        ]
        detail = _join_unique_clauses(selected)
        if detail:
            return f"{name}：仅保留当前镜头可见区域——{detail}", exclusions
        exclusions.append("不得为了复现完整场景设定而补入未写明的空间或地标")
        return f"{name}：场景参考图只锁定当前可见区域的空间、材质和光色", exclusions

    fallback = _clean_identity_text(
        _first_text(
            getattr(scene, "fixed_prompt", None),
            getattr(scene, "description", None),
            getattr(scene, "visual_style", None),
        )
    )
    clauses = _clauses(fallback)
    if close_shot:
        relevant = [item for item in clauses if _text_is_mentioned(item, visible_context)]
        detail = _join_unique_clauses(relevant)
        exclusions.append("不得为了复现完整场景设定而补入画外地标或远景")
        return (
            f"{name}：{detail or '场景参考图只锁定当前局部的光色与材质'}；其余空间虚化或画外",
            exclusions,
        )
    return f"{name}：{_join_unique_clauses(clauses)}", exclusions


def visible_prop_identities(props: Iterable[Any], *, visible_context: str) -> tuple[str, list[str]]:
    all_props = list(props)
    visible_props = select_visible_props(all_props, visible_context=visible_context)
    visible: list[str] = []
    for prop in visible_props:
        name = _text(getattr(prop, "name", None)) or "道具"
        spec = _mapping(getattr(prop, "asset_spec", None))
        parts: list[str] = []
        if spec:
            for field in ("shape", "dimensions"):
                _append(parts, _text(spec.get(field)))
            parts.extend(_string_list(spec.get("materials")))
            parts.extend(_string_list(spec.get("color_palette")))
            parts.extend(_string_list(spec.get("signature_features")))
        else:
            fallback = _clean_identity_text(
                _first_text(
                    getattr(prop, "visual_prompt", None),
                    getattr(prop, "description", None),
                )
            )
            parts.extend(_clauses(fallback))
        visible.append(f"{name}：{_join_unique_clauses(parts) or '按道具参考图保持可见外观'}")

    exclusions = []
    hidden_exclusion = hidden_entity_exclusion("道具", all_props, visible_props)
    if hidden_exclusion:
        exclusions.append(hidden_exclusion)
    return ("；".join(visible) if visible else "无当前镜头可见关键道具，不新增道具"), exclusions


def concise_reference_instruction(*, close_shot: bool) -> str:
    if close_shot:
        return "参考图只锁定当前可见部位的身份、比例、配色和线条；不得复制参考图的全身构图、姿态或补全画外元素"
    return "参考图锁定当前可见主体的身份、比例、配色和空间关系，不复制资产设定图排版"


def is_close_shot(shot_size: str) -> bool:
    return _is_close_shot(shot_size)


def sanitize_negative_prompt_for_bound_cast(value: str | None, *, bound_character_count: int) -> str | None:
    if not value:
        return value
    items = [item.strip() for item in re.split(r"[，,]\s*", value) if item.strip()]
    if bound_character_count > 1:
        contradictory_tokens = ("多角色", "多人物", "multiple characters", "multiple people", "multi-character")
        items = [
            item
            for item in items
            if not any(token in item.lower() for token in contradictory_tokens)
        ]
    return "，".join(dict.fromkeys(items))


def _entity_local_context(text: str, *, names: list[str], peer_names: list[str]) -> str:
    fragments = re.split(r"[，,；;。！？!？\n]+", text or "")
    result: list[str] = []
    target_names = [item for item in dict.fromkeys(names) if item]
    peer_names = [item for item in dict.fromkeys(peer_names) if item]
    active_target = False
    for fragment in fragments:
        target_positions = [fragment.find(name) for name in target_names if name in fragment]
        peer_positions = [fragment.find(name) for name in peer_names if name in fragment]
        target_position = min(target_positions) if target_positions else -1
        peer_position = min(peer_positions) if peer_positions else -1
        if target_position >= 0:
            end = peer_position if peer_position > target_position else len(fragment)
            result.append(fragment[target_position:end])
            active_target = peer_position < 0 or target_position > peer_position
        elif peer_position >= 0:
            active_target = False
        elif active_target and fragment:
            result.append(fragment)
    return "；".join(dict.fromkeys(item for item in result if item))


def _rank_relevant(items: Iterable[str], context: str) -> list[tuple[int, str]]:
    ranked = [(_relevance_score(item, context), item) for item in dict.fromkeys(item for item in items if item)]
    return sorted(ranked, key=lambda pair: pair[0], reverse=True)


def _relevance_score(value: str, context: str) -> int:
    left = _normalized_chars(value)
    right = _normalized_chars(context)
    if not left or not right:
        return 0
    if left in right or right in left:
        return 100
    left_bigrams = {left[index : index + 2] for index in range(max(0, len(left) - 1))}
    right_bigrams = {right[index : index + 2] for index in range(max(0, len(right) - 1))}
    return len(left_bigrams & right_bigrams) * 4 + len(set(left) & set(right))


def _text_is_mentioned(value: str, context: str) -> bool:
    return _relevance_score(value, context) >= 5


def _clause_is_explicitly_supported(clause: str, context: str) -> bool:
    fragments = [
        item.strip()
        for item in re.split(r"[，,、/]+", clause or "")
        if len(_normalized_chars(item)) >= 3
    ]
    normalized_context = _normalized_chars(context)
    return any(_normalized_chars(item) in normalized_context for item in fragments)


def _character_kind(character: Any) -> str:
    spec = _mapping(getattr(character, "asset_spec", None))
    kind = _text(spec.get("entity_kind")).lower()
    species = _text(spec.get("species")).lower()
    if kind in {"human", "person", "人物", "人类"} or _contains_any(species, ("人", "human")):
        return "human"
    if kind in {"animal", "动物"} or _contains_any(species, _ANIMAL_VISUAL_TOKENS):
        return "animal"
    name = _text(getattr(character, "name", None))
    if _contains_any(name, _ANIMAL_VISUAL_TOKENS):
        return "animal"
    return "unknown"


def _normalized_chars(value: str) -> str:
    return "".join(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", (value or "").lower()))


def _extract_light_clause(value: str) -> str:
    for clause in _clauses(value):
        if any(token in clause for token in ("光源", "光线", "照明", "色温")):
            return clause
    return ""


def _strip_unmentioned_accessories(value: str, accessories: list[str], context: str) -> str:
    hidden_nouns = {
        noun
        for accessory in accessories
        if not _text_is_mentioned(accessory, context)
        for noun in _ACCESSORY_NOUNS
        if noun in accessory
    }
    if not hidden_nouns:
        return value
    return "，".join(
        clause
        for clause in _clauses(value)
        if not any(noun in clause for noun in hidden_nouns)
    )


def _clean_identity_text(value: str) -> str:
    result = value
    for clause in _WORKFLOW_CLAUSES:
        result = result.replace(f"，{clause}", "").replace(clause, "")
    return result.strip(" ，,。；;")


def _join_unique_clauses(items: Iterable[str]) -> str:
    return "，".join(
        dict.fromkeys(
            _text(value).strip(" ，,。；;")
            for value in items
            if _text(value).strip(" ，,。；;")
        )
    )


def _clauses(value: str) -> list[str]:
    return [item.strip(" ，,。；;") for item in re.split(r"[，,；;。\n]+", value or "") if item.strip()]


def _is_close_shot(shot_size: str) -> bool:
    return _contains_any(shot_size, _CLOSE_SHOT_TOKENS)


def _contains_any(value: str, tokens: Iterable[str]) -> bool:
    lowered = (value or "").lower()
    return any(token.lower() in lowered for token in tokens)


def _append(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else re.split(r"[,，、\n]+", value) if isinstance(value, str) else []
    return list(dict.fromkeys(_text(item) for item in raw if _text(item)))


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""
