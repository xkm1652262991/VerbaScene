from __future__ import annotations

from copy import deepcopy
import hashlib
import re
from typing import Any

from app.agents.entity_design import (
    BLOCKING_MUTABLE_STATE_PATTERNS,
    REVIEW_MUTABLE_STATE_PATTERNS,
    compile_character_asset_fields,
    compile_prop_asset_fields,
    compile_scene_asset_fields,
    normalize_character_asset_spec,
    normalize_prop_asset_spec,
    normalize_scene_asset_spec,
)


_HUMAN_MARKERS = (
    "男孩",
    "女孩",
    "男生",
    "女生",
    "少年",
    "少女",
    "儿童",
    "小学生",
    "中学生",
    "青年",
    "老人",
    "老师",
    "父亲",
    "母亲",
    "妈妈",
    "爸爸",
    "human",
    "boy",
    "girl",
    "student",
    "teacher",
)
_ANIMAL_SPECIES = (
    (("麻雀",), "麻雀"),
    (("小鸟", "鸟", "bird"), "小型鸟类"),
    (("橘猫",), "橘猫"),
    (("小猫", "猫", "cat"), "家猫"),
    (("小狗", "狗", "dog"), "家犬"),
    (("兔", "rabbit"), "兔类"),
    (("狐狸", "fox"), "狐狸"),
    (("狼", "wolf"), "狼"),
    (("鱼", "fish"), "鱼类"),
    (("马", "horse"), "马"),
)
_ROBOT_MARKERS = ("机器人", "机械人", "仿生人", "robot", "android")

_CHARACTER_PALETTES = (
    ["深蓝色", "米白色", "暖棕色"],
    ["墨绿色", "浅卡其色", "砖红色"],
    ["深灰蓝色", "浅灰色", "琥珀色"],
    ["酒红色", "炭灰色", "米白色"],
)
_PROP_PALETTES = (
    ["浅棕色", "米白色", "深棕色"],
    ["深灰色", "银灰色", "低饱和蓝色"],
    ["暖白色", "浅木色", "低饱和红色"],
)

_CHARACTER_STATE_RULES = (
    ("受伤", re.compile(r"受伤|伤口|流血|骨折|翅膀.{0,6}(?:下垂|无法展开)|\bhurt\b|\binjured\b", re.I)),
    ("已康复", re.compile(r"康复|痊愈|伤愈|恢复健康|重新飞|recovered\b|\bhealed\b", re.I)),
    ("湿透", re.compile(r"湿透|浑身湿|soaked\b|wet through\b", re.I)),
    ("哭泣", re.compile(r"哭泣|流泪|crying\b|tears\b", re.I)),
    ("微笑", re.compile(r"微笑|笑起来|smiling\b|smiles\b", re.I)),
)
_PROP_STATE_RULES = (
    ("打开", re.compile(r"打开|张开|掀开|opened?\b", re.I)),
    ("关闭", re.compile(r"关闭|合上|盖上|closed?\b", re.I)),
    ("破损", re.compile(r"破损|裂开|折断|broken\b|damaged\b", re.I)),
    ("点亮", re.compile(r"点亮|发光|亮起|lit\b|glowing\b", re.I)),
)
_WEATHER_RULES = (
    ("雨天", re.compile(r"下雨|雨天|雨水|rain(?:ing|y)?\b", re.I)),
    ("雪天", re.compile(r"下雪|雪天|积雪|snow(?:ing|y)?\b", re.I)),
    ("雾天", re.compile(r"起雾|雾气|fog(?:gy)?\b", re.I)),
)

_COLOR_TRANSLATIONS = {
    "black": "黑色",
    "white": "白色",
    "blue": "蓝色",
    "red": "红色",
    "green": "绿色",
    "yellow": "黄色",
    "brown": "棕色",
    "gray": "灰色",
    "grey": "灰色",
    "orange": "橘色",
    "purple": "紫色",
}
_CHINESE_COLORS = (
    "黑色",
    "白色",
    "蓝色",
    "红色",
    "绿色",
    "黄色",
    "棕色",
    "灰色",
    "橘色",
    "紫色",
    "米白色",
    "卡其色",
    "灰褐色",
    "浅蓝色",
    "深蓝色",
)


def autofill_entity_asset_specs(
    result: dict[str, list[dict[str, Any]]],
    *,
    script_scenes: Any,
    script_content: str,
) -> dict[str, list[dict[str, Any]]]:
    """Fill sparse extraction output without inventing script evidence."""
    scenes = [dict(item) for item in script_scenes if isinstance(item, dict)] if isinstance(script_scenes, list) else []
    output = deepcopy(result)
    output["characters"] = [
        _autofill_character(item, scenes=scenes, script_content=script_content)
        for item in output.get("characters", [])
    ]
    output["scenes"] = [
        _autofill_scene(item, scenes=scenes, script_content=script_content)
        for item in output.get("scenes", [])
    ]
    output["props"] = [
        _autofill_prop(item, scenes=scenes, script_content=script_content)
        for item in output.get("props", [])
    ]
    return output


def _autofill_character(
    item: dict[str, Any],
    *,
    scenes: list[dict[str, Any]],
    script_content: str,
) -> dict[str, Any]:
    result = dict(item)
    spec = normalize_character_asset_spec(
        result.get("asset_spec"),
        legacy_appearance=_text(result.get("appearance")) or None,
    )
    filled: list[str] = []
    review: list[str] = []
    names = _entity_names(result.get("name"), spec.get("aliases"))
    matched = _matching_scenes(scenes, names=names, entity_type="character")
    context = _entity_context_text(result, matched, names)

    kind = _text(spec.get("entity_kind")) or _infer_character_kind(result, context)
    _fill(spec, "entity_kind", kind, filled)
    species = _text(spec.get("species")) or _infer_species(" ".join([*names, context]), kind)
    _fill(spec, "species", species, filled)

    defaults = _character_visual_defaults(
        name=_text(result.get("name")) or "角色",
        kind=kind,
        species=species,
        age=_text(result.get("age")),
        context=context,
    )
    for field in (
        "body_type",
        "facial_features",
        "hair_or_surface",
        "color_palette",
        "default_outfit",
        "signature_features",
    ):
        if field == "default_outfit" and kind != "human":
            continue
        _fill(spec, field, defaults.get(field), filled, review=review)

    if not spec.get("source_evidence"):
        _fill(
            spec,
            "source_evidence",
            _source_evidence(matched, names=names, script_content=script_content),
            filled,
        )
    merged_states, states_changed = _merge_character_states(
        spec.get("state_variants"),
        _character_states(matched),
    )
    if states_changed:
        spec["state_variants"] = merged_states
        _record("state_variants", filled)

    normalized = _sanitize_character_stable_identity(spec)
    for field in normalized:
        _record(field, review)

    raw_plan = _mapping(_mapping(result.get("asset_spec")).get("reference_plan"))
    current_plan = _mapping(spec.get("reference_plan"))
    if not raw_plan.get("views") and current_plan.get("required") is not False:
        views = _character_reference_views(kind=kind, species=species)
        spec["reference_plan"] = {
            **current_plan,
            "views": views,
        }
        _record("reference_plan.views", filled)

    _set_autofill_metadata(spec, filled=filled, normalized=normalized, review=review)
    spec = normalize_character_asset_spec(spec)
    result["asset_spec"] = spec
    result["appearance"], result["fixed_prompt"] = compile_character_asset_fields(
        name=_text(result.get("name")),
        identity=_optional_text(result.get("identity")),
        age=_optional_text(result.get("age")),
        gender=_optional_text(result.get("gender")),
        asset_spec=spec,
    )
    main_prompt = _mapping(_mapping(spec.get("image_prompts")).get("character_main_ref")).get("positive_prompt")
    if _text(main_prompt):
        result["fixed_prompt"] = _text(main_prompt)
    return result


def _autofill_scene(
    item: dict[str, Any],
    *,
    scenes: list[dict[str, Any]],
    script_content: str,
) -> dict[str, Any]:
    result = dict(item)
    spec = normalize_scene_asset_spec(
        result.get("asset_spec"),
        legacy_description=_optional_text(result.get("description")),
    )
    filled: list[str] = []
    review: list[str] = []
    names = _entity_names(result.get("name"), spec.get("aliases"))
    matched = _matching_scenes(scenes, names=names, entity_type="scene")
    profile = _scene_visual_defaults(_text(result.get("name")), matched)

    for field in (
        "location_type",
        "spatial_layout",
        "fixed_landmarks",
        "materials",
        "color_palette",
        "zones",
    ):
        _fill(spec, field, profile.get(field), filled, review=review)
    if not spec.get("source_evidence"):
        _fill(
            spec,
            "source_evidence",
            _source_evidence(matched, names=names, script_content=script_content),
            filled,
        )
    if not spec.get("state_variants"):
        _fill(spec, "state_variants", _scene_states(matched), filled)

    raw_plan = _mapping(_mapping(result.get("asset_spec")).get("reference_plan"))
    current_plan = _mapping(spec.get("reference_plan"))
    if not raw_plan.get("views") and current_plan.get("required") is not False:
        zones = [str(value) for value in spec.get("zones", []) if value]
        spec["reference_plan"] = {
            **current_plan,
            "views": ["空间全景", *[f"{zone}区域视图" for zone in zones[:2]]],
        }
        _record("reference_plan.views", filled)

    _set_autofill_metadata(spec, filled=filled, normalized=[], review=review)
    spec = normalize_scene_asset_spec(spec)
    result["asset_spec"] = spec
    (
        result["description"],
        result["visual_style"],
        result["atmosphere"],
        result["fixed_prompt"],
    ) = compile_scene_asset_fields(name=_text(result.get("name")), asset_spec=spec)
    scene_prompt = _mapping(_mapping(spec.get("image_prompts")).get("scene_ref")).get("positive_prompt")
    if _text(scene_prompt):
        result["fixed_prompt"] = _text(scene_prompt)
    return result


def _autofill_prop(
    item: dict[str, Any],
    *,
    scenes: list[dict[str, Any]],
    script_content: str,
) -> dict[str, Any]:
    result = dict(item)
    spec = normalize_prop_asset_spec(
        result.get("asset_spec"),
        legacy_description=_optional_text(result.get("description")),
        legacy_story_function=_optional_text(result.get("story_function")),
    )
    filled: list[str] = []
    review: list[str] = []
    names = _entity_names(result.get("name"), spec.get("aliases"))
    matched = _matching_scenes(scenes, names=names, entity_type="prop")
    profile = _prop_visual_defaults(_text(result.get("name")))

    for field in (
        "shape",
        "dimensions",
        "materials",
        "color_palette",
        "signature_features",
        "scale_reference",
    ):
        _fill(spec, field, profile.get(field), filled, review=review)
    if not spec.get("holder_relation"):
        _fill(spec, "holder_relation", _holder_relation(matched), filled)
    if not spec.get("story_function"):
        _fill(spec, "story_function", _story_function(matched), filled)
    if not spec.get("source_evidence"):
        _fill(
            spec,
            "source_evidence",
            _source_evidence(matched, names=names, script_content=script_content),
            filled,
        )
    if not spec.get("state_variants"):
        _fill(spec, "state_variants", _prop_states(matched), filled)

    raw_plan = _mapping(_mapping(result.get("asset_spec")).get("reference_plan"))
    current_plan = _mapping(spec.get("reference_plan"))
    if not raw_plan.get("views") and current_plan.get("required") is not False:
        spec["reference_plan"] = {
            **current_plan,
            "views": ["正面三分之四视角", "侧面尺度视图"],
        }
        _record("reference_plan.views", filled)

    _set_autofill_metadata(spec, filled=filled, normalized=[], review=review)
    spec = normalize_prop_asset_spec(spec)
    result["asset_spec"] = spec
    result["description"], result["visual_prompt"], result["story_function"] = compile_prop_asset_fields(
        name=_text(result.get("name")),
        asset_spec=spec,
    )
    prop_prompt = _mapping(_mapping(spec.get("image_prompts")).get("prop_ref")).get("positive_prompt")
    if _text(prop_prompt):
        result["visual_prompt"] = _text(prop_prompt)
    return result


def _infer_character_kind(item: dict[str, Any], context: str) -> str:
    text = " ".join(
        _text(value)
        for value in (
            item.get("name"),
            item.get("identity"),
            item.get("role_type"),
            item.get("age"),
            item.get("gender"),
            context,
        )
        if _text(value)
    ).lower()
    if _infer_species(text, ""):
        return "animal"
    if any(marker in text for marker in _ROBOT_MARKERS):
        return "robot"
    if any(marker in text for marker in _HUMAN_MARKERS) or item.get("age") or item.get("gender"):
        return "human"
    return "creature"


def _infer_species(text: str, kind: str) -> str:
    lowered = text.lower()
    for markers, species in _ANIMAL_SPECIES:
        if any(marker in lowered for marker in markers):
            return species
    if kind == "human":
        return "人类"
    if kind == "robot":
        return "机器人"
    if kind == "creature":
        return "虚构生物"
    return ""


def _character_visual_defaults(
    *,
    name: str,
    kind: str,
    species: str,
    age: str,
    context: str,
) -> dict[str, Any]:
    explicit_palette = _extract_colors(" ".join((name, context)))
    if kind == "animal":
        palette = explicit_palette or (
            ["灰褐色", "浅米色", "深灰色"]
            if "鸟" in species or "麻雀" in species
            else _stable_palette(name, _CHARACTER_PALETTES)
        )
        if "鸟" in species or "麻雀" in species:
            return {
                "body_type": "小型鸟类自然体型，躯干圆润，头身与翼展比例稳定",
                "facial_features": "小型圆眼和短喙，眼周与面部羽区边界清晰",
                "hair_or_surface": "短羽覆盖方向稳定，飞羽和尾羽轮廓清楚",
                "color_palette": palette,
                "default_outfit": "",
                "signature_features": ["短喙轮廓固定", "飞羽与尾羽长度比例稳定"],
            }
        return {
            "body_type": f"{species or '动物'}自然体型，头身、四肢与尾部比例稳定",
            "facial_features": "眼睛、耳部和口鼻轮廓清晰，面部色块边界固定",
            "hair_or_surface": "皮毛或表面覆盖方向稳定，主要色块位置固定",
            "color_palette": palette,
            "default_outfit": "",
            "signature_features": ["头部外轮廓固定", "面部色块位置稳定"],
        }
    if kind == "robot":
        palette = explicit_palette or _stable_palette(name, _PROP_PALETTES)
        return {
            "body_type": "双足仿生机械结构，头身与关节比例稳定",
            "facial_features": "面部显示区与感应器位置固定，轮廓简洁",
            "hair_or_surface": "哑光机械外壳，拼接线和关节位置固定",
            "color_palette": palette,
            "default_outfit": "",
            "signature_features": ["头部感应器位置固定", "关节拼接线清晰"],
        }

    palette = explicit_palette or _stable_palette(name, _CHARACTER_PALETTES)
    if re.search(r"小学生|儿童|孩子|(?:[6-9]|1[0-2])s*岁", age, re.I):
        body_type = "儿童自然体型，头身比约 5.5:1，四肢比例简洁稳定"
    elif re.search(r"少年|少女|中学生|1[3-7]s*岁", age, re.I):
        body_type = "青少年自然体型，头身比约 6.5:1，四肢比例稳定"
    else:
        body_type = "自然人体比例，头身、肩宽与四肢比例在多镜头中稳定"
    return {
        "body_type": body_type,
        "facial_features": "面部轮廓简洁，五官比例自然，眼睛与眉形边界清晰",
        "hair_or_surface": "发型外轮廓简洁，发束方向和发际线位置固定",
        "color_palette": palette,
        "default_outfit": f"{palette[0]}简洁上衣，{palette[1]}下装，无文字和复杂图案",
        "signature_features": ["发型外轮廓固定", "服装主色块边界清晰"],
    }


def _character_reference_views(*, kind: str, species: str) -> list[str]:
    if kind == "animal" and ("鸟" in species or "麻雀" in species):
        return ["侧面全身", "正面近景", "展翼姿态参考"]
    if kind == "animal":
        return ["侧面全身", "正面近景", "标准站立姿态"]
    if kind == "robot":
        return ["正面全身", "侧面全身", "关节结构近景"]
    return ["正面全身", "侧面全身", "面部近景"]


def _scene_visual_defaults(name: str, matched: list[dict[str, Any]]) -> dict[str, Any]:
    text = " ".join([name, *[_scene_text(scene) for scene in matched]]).lower()
    if any(marker in text for marker in ("树下", "树林", "树枝", "草地", "tree", "forest")):
        return {
            "location_type": "室外自然场景",
            "spatial_layout": f"{name}以主树干为空间锚点，树下活动区、前景地面和背景植被层次固定",
            "fixed_landmarks": ["主树干", "树下地面", "背景植被"],
            "materials": ["树皮", "草叶", "自然土地"],
            "color_palette": ["树皮棕", "草叶绿", "暖日光色"],
            "zones": ["树干附近", "树下活动区", "背景植被区"],
        }
    if any(marker in text for marker in ("窗台", "窗外", "window")):
        return {
            "location_type": "窗边过渡空间",
            "spatial_layout": f"{name}以窗框为稳定取景边界，室内窗台、窗外近景和远景层次固定",
            "fixed_landmarks": ["固定窗框", "平整窗台", "窗外背景"],
            "materials": ["哑光窗框", "透明玻璃", "墙面"],
            "color_palette": ["暖白色", "浅木色", "低饱和蓝绿色"],
            "zones": ["室内窗台", "窗外近景", "远景"],
        }
    if any(marker in text for marker in ("房", "室内", "家中", "room", "bedroom", "classroom")):
        return {
            "location_type": "室内生活空间",
            "spatial_layout": f"{name}以主要活动区为画面中心，入口、家具区和窗边区的方位关系固定",
            "fixed_landmarks": ["固定入口", "主要家具", "窗边区域"],
            "materials": ["浅色墙面", "哑光木质家具", "低反光织物"],
            "color_palette": ["暖白色", "浅木色", "低饱和蓝绿色"],
            "zones": ["主要活动区", "家具区", "窗边区"],
        }
    return {
        "location_type": "固定剧情场所",
        "spatial_layout": f"{name}以主要活动区为核心，入口、主地标与前中后景关系保持固定",
        "fixed_landmarks": ["主要活动区", "固定入口", "背景空间锚点"],
        "materials": ["哑光主材质", "低反光辅助材质"],
        "color_palette": _stable_palette(name, _PROP_PALETTES),
        "zones": ["主要活动区", "入口区", "背景区"],
    }


def _prop_visual_defaults(name: str) -> dict[str, Any]:
    lowered = name.lower()
    profiles: tuple[tuple[tuple[str, ...], dict[str, Any]], ...] = (
        (("纸盒", "cardboard box"), {
            "shape": "浅口长方体纸盒",
            "dimensions": "儿童可用双手稳定托起",
            "materials": ["哑光瓦楞纸"],
            "color_palette": ["浅棕色", "米白色"],
            "signature_features": ["可翻折盒盖", "清晰纸面纹理"],
            "scale_reference": "宽度约为儿童肩宽的一半，跨镜头保持一致",
        }),
        (("滴管", "dropper"), {
            "shape": "细长透明管身和柔软胶头",
            "dimensions": "可由角色单手拇指与食指持握",
            "materials": ["透明塑料", "哑光软胶"],
            "color_palette": ["透明色", "浅米黄色", "灰色刻度"],
            "signature_features": ["管身刻度位置固定", "胶头与管身连接处清晰"],
            "scale_reference": "长度约为角色手掌长度的三分之二",
        }),
        (("面包屑", "bread crumb"), {
            "shape": "不规则小片状软质面包",
            "dimensions": "单片约指甲盖大小",
            "materials": ["松软面包组织"],
            "color_palette": ["米白色", "浅黄色"],
            "signature_features": ["边缘轻微卷曲", "内部气孔细小"],
            "scale_reference": "单片小于角色指尖宽度",
        }),
        (("书包", "backpack"), {
            "shape": "圆角长方体双肩包",
            "dimensions": "与持有角色背部宽度匹配",
            "materials": ["哑光织物", "树脂拉链"],
            "color_palette": ["低饱和红色", "深灰色"],
            "signature_features": ["前袋轮廓固定", "双肩带位置对称"],
            "scale_reference": "高度约为持有角色躯干高度的一半",
        }),
        (("书", "book"), {
            "shape": "薄型长方体书本",
            "dimensions": "可由角色单手或双手持握",
            "materials": ["哑光纸张", "硬质封面"],
            "color_palette": ["深蓝色", "米白色"],
            "signature_features": ["书脊厚度固定", "封面无可读文字"],
            "scale_reference": "宽度约为角色肩宽的三分之一",
        }),
    )
    for markers, profile in profiles:
        if any(marker in lowered for marker in markers):
            return profile
    palette = _stable_palette(name, _PROP_PALETTES)
    return {
        "shape": f"单一「{name}」主体，外轮廓简洁且跨镜头固定",
        "dimensions": "与角色手部或邻近物体形成清晰尺度对比",
        "materials": ["哑光主材质"],
        "color_palette": palette,
        "signature_features": ["外轮廓比例固定", "关键开口或连接结构位置固定"],
        "scale_reference": "与首张确认参考图中的角色手部比例保持一致",
    }


def _matching_scenes(
    scenes: list[dict[str, Any]],
    *,
    names: list[str],
    entity_type: str,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for scene in scenes:
        if entity_type == "character":
            listed = _string_values(scene.get("characters"))
            direct = _list_matches(listed, names)
        elif entity_type == "prop":
            listed = _string_values(scene.get("props"))
            direct = _list_matches(listed, names)
        else:
            listed = [_text(scene.get("title")), _text(scene.get("location"))]
            direct = _list_matches(listed, names, allow_partial=True)
        if direct or any(_contains_name(_scene_text(scene), name) for name in names):
            matches.append(scene)
    return matches


def _source_evidence(
    matched: list[dict[str, Any]],
    *,
    names: list[str],
    script_content: str,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for scene in matched[:3]:
        text = _scene_evidence_text(scene)
        if not text:
            continue
        evidence.append({"scene_no": _positive_int(scene.get("scene_no")), "evidence": text})
    if evidence:
        return evidence
    sentence = _sentence_with_name(script_content, names)
    return [{"scene_no": None, "evidence": sentence}] if sentence else []


def _character_states(matched: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _pattern_states(matched, _CHARACTER_STATE_RULES)


def _merge_character_states(existing: Any, inferred: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    merged = deepcopy(existing) if isinstance(existing, list) else []
    changed = not isinstance(existing, list) and bool(inferred)
    for candidate in inferred:
        candidate_family = _character_state_family(candidate)
        matched = next(
            (
                state
                for state in merged
                if isinstance(state, dict)
                and (
                    _character_state_family(state) == candidate_family
                    if candidate_family
                    else _normalized(state.get("name")) == _normalized(candidate.get("name"))
                )
            ),
            None,
        )
        if matched is None:
            merged.append(deepcopy(candidate))
            changed = True
            continue
        scene_nos = [
            number
            for number in (
                _positive_int(value)
                for value in [*matched.get("scene_nos", []), *candidate.get("scene_nos", [])]
            )
            if number is not None
        ]
        deduplicated = list(dict.fromkeys(scene_nos))
        if deduplicated != matched.get("scene_nos", []):
            matched["scene_nos"] = deduplicated
            changed = True
    return merged, changed


def _character_state_family(state: dict[str, Any]) -> str:
    text = "；".join((_text(state.get("name")), _text(state.get("description"))))
    for family, pattern in _CHARACTER_STATE_RULES:
        if pattern.search(text):
            return family
    return ""


def _sanitize_character_stable_identity(spec: dict[str, Any]) -> list[str]:
    normalized: list[str] = []
    for field in ("body_type", "facial_features", "hair_or_surface", "default_outfit"):
        current = _text(spec.get(field))
        cleaned = _without_mutable_state_clauses(current)
        if cleaned != current:
            spec[field] = cleaned
            _record(field, normalized)
    for field in ("signature_features", "default_accessories"):
        current_values = _string_values(spec.get(field))
        cleaned_values = [
            cleaned
            for value in current_values
            if (cleaned := _without_mutable_state_clauses(value))
        ]
        if cleaned_values != current_values:
            spec[field] = cleaned_values
            _record(field, normalized)
    return normalized


def _without_mutable_state_clauses(value: str) -> str:
    if not value:
        return ""
    patterns = (*BLOCKING_MUTABLE_STATE_PATTERNS, *REVIEW_MUTABLE_STATE_PATTERNS)
    stable_sections: list[str] = []
    for section in re.split(r"[；;。]+", value):
        stable_parts = [
            part.strip()
            for part in re.split(r"[，,]+", section)
            if part.strip() and not any(pattern.search(part) for pattern in patterns)
        ]
        if stable_parts:
            stable_sections.append("，".join(stable_parts))
    return "；".join(stable_sections)


def _prop_states(matched: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _pattern_states(matched, _PROP_STATE_RULES)


def _pattern_states(
    matched: list[dict[str, Any]],
    rules: tuple[tuple[str, re.Pattern[str]], ...],
) -> list[dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    for scene in matched:
        text = _scene_text(scene)
        scene_no = _positive_int(scene.get("scene_no"))
        for name, pattern in rules:
            if not pattern.search(text):
                continue
            state = states.setdefault(name, {"name": name, "description": "", "scene_nos": []})
            if not state["description"]:
                state["description"] = _field_with_pattern(scene, pattern) or _clip(text, 120)
            if scene_no is not None and scene_no not in state["scene_nos"]:
                state["scene_nos"].append(scene_no)
    return list(states.values())


def _scene_states(matched: list[dict[str, Any]]) -> list[dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    for scene in matched:
        scene_no = _positive_int(scene.get("scene_no"))
        time_of_day = _text(scene.get("time_of_day"))
        if time_of_day and time_of_day not in {"未指定", "不详", "unknown"}:
            description = f"时间状态：{time_of_day}"
            mood = _text(scene.get("mood"))
            if mood:
                description = f"{description}；场次氛围：{mood}"
            _merge_state(states, name=time_of_day, description=description, scene_no=scene_no)
        text = _scene_text(scene)
        for name, pattern in _WEATHER_RULES:
            if pattern.search(text):
                _merge_state(
                    states,
                    name=name,
                    description=_field_with_pattern(scene, pattern) or _clip(text, 120),
                    scene_no=scene_no,
                )
    return list(states.values())


def _merge_state(
    states: dict[str, dict[str, Any]],
    *,
    name: str,
    description: str,
    scene_no: int | None,
) -> None:
    state = states.setdefault(name, {"name": name, "description": description, "scene_nos": []})
    if scene_no is not None and scene_no not in state["scene_nos"]:
        state["scene_nos"].append(scene_no)


def _holder_relation(matched: list[dict[str, Any]]) -> str:
    for scene in matched:
        characters = _string_values(scene.get("characters"))
        if characters:
            return f"{characters[0]}在相关场次中与此道具发生交互"
    return ""


def _story_function(matched: list[dict[str, Any]]) -> str:
    for scene in matched:
        purpose = _text(scene.get("story_purpose"))
        if purpose:
            return purpose
        action = _text(scene.get("visible_action"))
        if action:
            return f"用于支撑场次动作：{_clip(action, 80)}"
    return ""


def _scene_evidence_text(scene: dict[str, Any]) -> str:
    for field in ("source_evidence", "visible_action", "story_purpose", "start_state", "end_state"):
        value = _text(scene.get(field))
        if value:
            return _clip(value, 140)
    title = _text(scene.get("title"))
    location = _text(scene.get("location"))
    return "：".join(value for value in (title, location) if value)


def _scene_text(scene: dict[str, Any]) -> str:
    parts: list[str] = []
    for field in (
        "title",
        "location",
        "time_of_day",
        "visible_action",
        "story_purpose",
        "start_state",
        "end_state",
        "mood",
        "source_evidence",
    ):
        value = _text(scene.get(field))
        if value:
            parts.append(value)
    parts.extend(_string_values(scene.get("characters")))
    parts.extend(_string_values(scene.get("props")))
    for dialogue in scene.get("dialogues", []) if isinstance(scene.get("dialogues"), list) else []:
        if isinstance(dialogue, dict):
            parts.extend([_text(dialogue.get("speaker")), _text(dialogue.get("text"))])
    return "；".join(part for part in parts if part)


def _entity_context_text(
    item: dict[str, Any],
    matched: list[dict[str, Any]],
    names: list[str],
) -> str:
    parts = [
        _text(item.get("identity")),
        _text(item.get("appearance")),
        _text(item.get("fixed_prompt")),
    ]
    for scene in matched:
        for field in ("visible_action", "start_state", "end_state", "source_evidence"):
            value = _text(scene.get(field))
            if value and any(_contains_name(value, name) for name in names):
                parts.append(value)
    return "；".join(part for part in parts if part)


def _field_with_pattern(scene: dict[str, Any], pattern: re.Pattern[str]) -> str:
    for field in ("visible_action", "start_state", "end_state", "source_evidence", "story_purpose"):
        value = _text(scene.get(field))
        if value and pattern.search(value):
            return _clip(value, 120)
    return ""


def _sentence_with_name(content: str, names: list[str]) -> str:
    for sentence in re.split(r"(?<=[。！？.!?])|\n+", content or ""):
        cleaned = sentence.strip()
        if cleaned and any(_contains_name(cleaned, name) for name in names):
            return _clip(cleaned, 140)
    return ""


def _extract_colors(text: str) -> list[str]:
    colors: list[str] = []
    lowered = text.lower()
    for color in _CHINESE_COLORS:
        if color in text and color not in colors:
            colors.append(color)
    for english, chinese in _COLOR_TRANSLATIONS.items():
        if re.search(rf"\b{re.escape(english)}\b", lowered) and chinese not in colors:
            colors.append(chinese)
    return colors[:4]


def _stable_palette(name: str, palettes: tuple[list[str], ...]) -> list[str]:
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return list(palettes[digest[0] % len(palettes)])


def _entity_names(name: Any, aliases: Any) -> list[str]:
    values = [_text(name), *_string_values(aliases)]
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _list_matches(values: list[str], names: list[str], *, allow_partial: bool = False) -> bool:
    normalized_values = [_normalized(value) for value in values if value]
    for name in names:
        normalized_name = _normalized(name)
        if not normalized_name:
            continue
        for value in normalized_values:
            if value == normalized_name:
                return True
            if allow_partial and (value in normalized_name or normalized_name in value):
                return True
    return False


def _contains_name(text: str, name: str) -> bool:
    if not text or not name:
        return False
    if re.fullmatch(r"[A-Za-z0-9 _-]+", name):
        return re.search(rf"\b{re.escape(name.strip())}\b", text, re.I) is not None
    compact_name = _normalized(name)
    return len(compact_name) >= 2 and compact_name in _normalized(text)


def _fill(
    target: dict[str, Any],
    field: str,
    value: Any,
    filled: list[str],
    *,
    review: list[str] | None = None,
) -> None:
    if _has_value(target.get(field)) or not _has_value(value):
        return
    target[field] = value
    _record(field, filled)
    if review is not None:
        _record(field, review)


def _set_autofill_metadata(
    spec: dict[str, Any],
    *,
    filled: list[str],
    normalized: list[str],
    review: list[str],
) -> None:
    existing = _mapping(spec.get("autofill"))
    merged_filled = _merge_strings(existing.get("filled_fields"), filled)
    merged_normalized = _merge_strings(existing.get("normalized_fields"), normalized)
    merged_review = _merge_strings(existing.get("review_fields"), review)
    spec["autofill"] = {
        "applied": bool(merged_filled or merged_normalized) or existing.get("applied") is True,
        "filled_fields": merged_filled,
        "normalized_fields": merged_normalized,
        "review_fields": merged_review,
    }


def _merge_strings(left: Any, right: Any) -> list[str]:
    result: list[str] = []
    for value in [*_string_values(left), *_string_values(right)]:
        if value not in result:
            result.append(value)
    return result


def _record(value: str, values: list[str]) -> None:
    if value and value not in values:
        values.append(value)


def _has_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return value is not None


def _string_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _text(item))]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _optional_text(value: Any) -> str | None:
    return _text(value) or None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _normalized(value: Any) -> str:
    return re.sub(r"[\s　，。；：:,.!?！？、\-_]+", "", _text(value)).lower()


def _clip(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text if len(text) <= limit else f"{text[: limit - 1].rstrip()}…"
