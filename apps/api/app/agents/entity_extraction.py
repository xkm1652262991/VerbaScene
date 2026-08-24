import json
import re
from typing import Any

from app.models import Project, Script
from app.agents.style import project_style_prompt_line, strip_project_style
from app.agents.entity_design import (
    compile_character_asset_fields,
    compile_prop_asset_fields,
    compile_scene_asset_fields,
    normalize_character_asset_spec,
    normalize_prop_asset_spec,
    normalize_scene_asset_spec,
)


def build_entity_extraction_prompt(
    project: Project,
    script: Script,
    *,
    style_sentence: str | None = None,
) -> str:
    return f"""
你是动画资产设定师。请严格基于已确认的结构化生产剧本，建立可复用的角色、场景和道具资产卡。媒介风格只服从项目设置，不自行预设二维或三维。

硬性要求：
- 只输出 JSON，不要输出解释、Markdown、代码块。
- JSON 顶层格式必须是 {{"characters": [...], "scenes": [...], "props": [...]}}。
- 不要套用示例资产，不要新增与剧本无关的追捕者、倒计时手环、霓虹雨夜等元素，除非剧本中明确出现。
- 必须保留剧本中的人物姓名、身份关系、关键地点、关键道具和剧情功能，并为每个资产写出来源场次与事实依据。
- 所有文本字段使用中文；role_type 可使用 protagonist / antagonist / supporting，系统会转为中文。
- 人物等专有名称的 name 可以保留原文，JSON 字段名、id 和约定枚举值也可以保留合同规定的英文；除此之外，所有资产描述、状态、证据概括和参考图 Prompt 必须使用自然、完整的简体中文。
- 上游剧本含有英文或其他语言时，先翻译其含义再编写资产卡，不得把外文动作、地点、物品、外观或完整描述句直接复制进 Prompt；普通名词如 kitchen、cookie jar 必须写成“厨房”“饼干罐”。
- source_evidence 必须用中文概括事实，不长段照抄外文原句。
- 不要输出 fixed_prompt、appearance、description、visual_style、atmosphere 或 visual_prompt；这些兼容字段由系统根据 asset_spec 确定性编译。
- 同一次调用必须直接输出每个实体的参考图主体自然语言 Prompt，不再交给通用模板补写主体事实。
- positive_prompt 只描述主体身份、造型、材质事实、视角和构图，不得复制或改写项目视觉风格；系统会在图片 Provider 调用时统一注入当前风格。
- Prompt 使用连贯、具体的中文自然语言，不使用“风格DNA、角色身份、场景身份、道具身份、画外排除”等固定栏目。
- negative_prompt 默认只写“字幕，水印，logo，可读文字”；仅在当前参考图确有必要时增加明确约束。
- 具有自主动作、表情或剧情行为的人、动物、怪物、精灵和自主机器人必须归入 characters，绝不能归入 props。
- 受伤、康复、哭泣、姿态、正在手持、服装临时变化属于 state_variants，不能写进稳定身份字段；每个变体必须有稳定英文小写 key。
- 时间、天气、季节、光线和损坏状态属于场景 state_variants，不能写进固定空间身份。
- 同一基础地点的窗台、书桌、门口等优先作为 zones，不要拆成互不相关的场景；只有空间结构明显不同才建立新场景。
- 只把需要跨镜头绑定的物件列为 props。一次性背景碎屑可设 reference_plan.required=false、priority=optional。
- 不要输出英文视觉提示词，也不要把项目动画风格复制进每个资产身份。
- 资产提取结果必须能直接进入人工审核，不要把必需视觉字段留空后交给用户从头填写。
- 剧本未明说外观时，可以根据实体类型、年龄、身份和剧情用途选择克制、可生成、易保持一致的生产设计；但 source_evidence 只能记录剧本事实，不得用“依据剧本设定”等套话伪造依据。
- 只有语义上不适用的字段可以为空：动物的 default_outfit、没有别名的 aliases、没有默认配件的 default_accessories、没有临时状态的 state_variants。
- autofill 是系统写入的补全记录，模型不要输出该字段。

characters 每项必须包含：
- name: string
- role_type: string|null，例如 主角、反派、配角
- age: string|null
- gender: string|null
- identity: string|null
- personality: string|null
- asset_spec: object，必须包含：
  - schema_version: 1
  - aliases: string[]
  - entity_kind: string，例如 human / animal / creature / robot
  - species: string
  - body_type: string
  - facial_features: string
  - hair_or_surface: string，人物写头发，动物写羽毛/皮毛/鳞片
  - color_palette: string[]
  - default_outfit: string，动物无服装时返回空字符串
  - signature_features: string[]
  - default_accessories: string[]
  - state_variants: array，每项包含 key、name、description、scene_nos:number[]
  - source_evidence: array，每项包含 scene_no:number|null、evidence:string
  - reference_plan: object，包含 required:boolean、priority:core|supporting|optional、views:string[]
- reference_prompts: object，只包含 character_main_ref，字段为 positive_prompt、negative_prompt
  - positive_prompt 必须直接描述一张 16:9 横向多视角角色设定图：同一角色、同一服装与配色，在同一张图中依次展示正面全身、四分之三全身、侧面全身和脸部近景，背景简洁，视角之间不得改变身份特征
  - 不再分别生成脸部图和全身图

scenes 每项必须包含 name 和 asset_spec。asset_spec 必须包含：
- schema_version: 1
- aliases: string[]
- location_type: string
- spatial_layout: string
- fixed_landmarks: string[]
- materials: string[]
- color_palette: string[]
- zones: string[]
- state_variants: array，每项包含 key、name、description、scene_nos:number[]
- source_evidence: array，每项包含 scene_no:number|null、evidence:string
- reference_plan: object，包含 required:boolean、priority:core|supporting|optional、views:string[]
- reference_prompt: object，包含 positive_prompt、negative_prompt

props 每项必须包含 name 和 asset_spec。asset_spec 必须包含：
- schema_version: 1
- aliases: string[]
- shape: string
- dimensions: string
- materials: string[]
- color_palette: string[]
- signature_features: string[]
- scale_reference: string
- holder_relation: string
- story_function: string
- state_variants: array，每项包含 key、name、description、scene_nos:number[]
- source_evidence: array，每项包含 scene_no:number|null、evidence:string
- reference_plan: object，包含 required:boolean、priority:core|supporting|optional、views:string[]
- reference_prompt: object，包含 positive_prompt、negative_prompt

项目标题：
{project.title}

{project_style_prompt_line(project.style)}

结构化生产场景（资产事实与状态的主数据）：
{json.dumps(script.scenes if isinstance(script.scenes, list) else [], ensure_ascii=False, indent=2)}

可读剧本（用于补充理解，不得覆盖结构化场景）：
{script.content}
""".strip()


def parse_entity_extraction_response(
    text: str,
    *,
    style_sentence: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    payload = _loads_json_object(text)
    characters = _normalize_characters(payload.get("characters"), style_sentence=style_sentence)
    scenes = _normalize_scenes(payload.get("scenes"), style_sentence=style_sentence)
    props = _normalize_props(payload.get("props"), style_sentence=style_sentence)
    if not characters:
        raise ValueError("Entity response must contain at least one character")
    if not scenes:
        raise ValueError("Entity response must contain at least one scene")
    return {"characters": characters, "scenes": scenes, "props": props}


def _loads_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    payload = json.loads(cleaned)
    if not isinstance(payload, dict):
        raise ValueError("Entity response JSON must be an object")
    return payload


def _normalize_characters(value: Any, *, style_sentence: str | None = None) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    characters = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        name = _string_or_none(item.get("name"))
        if not name:
            continue
        identity = _string_or_none(item.get("identity"))
        personality = _string_or_none(item.get("personality"))
        raw_asset_spec = item.get("asset_spec") if isinstance(item.get("asset_spec"), dict) else {}
        asset_spec = normalize_character_asset_spec(
            raw_asset_spec,
            legacy_appearance=_string_or_none(item.get("appearance")),
            default_reference_views=False,
        )
        appearance, fixed_prompt = compile_character_asset_fields(
            name=name,
            identity=identity,
            age=_string_or_none(item.get("age")),
            gender=_localized_gender(_string_or_none(item.get("gender"))),
            asset_spec=asset_spec,
        )
        image_prompts = _normalize_character_reference_prompts(
            item.get("reference_prompts")
            or raw_asset_spec.get("reference_prompts")
            or raw_asset_spec.get("image_prompts"),
            fallback=f"{name}，{fixed_prompt or appearance}",
            style_sentence=style_sentence,
            index=index,
        )
        if image_prompts:
            asset_spec["image_prompts"] = image_prompts
        characters.append(
            {
                "name": name,
                "role_type": _localized_role_type(_string_or_none(item.get("role_type"))),
                "age": _string_or_none(item.get("age")),
                "gender": _localized_gender(_string_or_none(item.get("gender"))),
                "identity": identity,
                "personality": personality,
                "appearance": appearance,
                "fixed_prompt": (
                    image_prompts["character_main_ref"]["positive_prompt"]
                    if image_prompts and image_prompts.get("character_main_ref")
                    else fixed_prompt
                ),
                "asset_spec": asset_spec,
            }
        )
    return characters


def _normalize_scenes(value: Any, *, style_sentence: str | None = None) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    scenes = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        name = _string_or_none(item.get("name"))
        if not name:
            continue
        raw_asset_spec = item.get("asset_spec") if isinstance(item.get("asset_spec"), dict) else {}
        asset_spec = normalize_scene_asset_spec(
            raw_asset_spec,
            legacy_description=_string_or_none(item.get("description")),
            default_reference_views=False,
        )
        description, visual_style, atmosphere, fixed_prompt = compile_scene_asset_fields(
            name=name,
            asset_spec=asset_spec,
        )
        reference_prompt = _normalize_reference_prompt(
            item.get("reference_prompt")
            or raw_asset_spec.get("reference_prompt")
            or _nested_prompt(raw_asset_spec.get("image_prompts"), "scene_ref"),
            fallback=f"{name}，{fixed_prompt or description}",
            style_sentence=style_sentence,
            context=f"Scene {index} reference_prompt",
        )
        if reference_prompt:
            asset_spec["image_prompts"] = {"scene_ref": reference_prompt}
        scenes.append(
            {
                "name": name,
                "description": description,
                "visual_style": visual_style,
                "atmosphere": atmosphere,
                "fixed_prompt": reference_prompt["positive_prompt"] if reference_prompt else fixed_prompt,
                "asset_spec": asset_spec,
            }
        )
    return scenes


def _normalize_props(value: Any, *, style_sentence: str | None = None) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    props = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            continue
        name = _string_or_none(item.get("name"))
        if not name:
            continue
        raw_asset_spec = item.get("asset_spec") if isinstance(item.get("asset_spec"), dict) else {}
        asset_spec = normalize_prop_asset_spec(
            raw_asset_spec,
            legacy_description=_string_or_none(item.get("description")),
            legacy_story_function=_string_or_none(item.get("story_function")),
            default_reference_views=False,
        )
        description, visual_prompt, story_function = compile_prop_asset_fields(
            name=name,
            asset_spec=asset_spec,
        )
        reference_prompt = _normalize_reference_prompt(
            item.get("reference_prompt")
            or raw_asset_spec.get("reference_prompt")
            or _nested_prompt(raw_asset_spec.get("image_prompts"), "prop_ref"),
            fallback=f"{name}，{visual_prompt or description}",
            style_sentence=style_sentence,
            context=f"Prop {index} reference_prompt",
        )
        if reference_prompt:
            asset_spec["image_prompts"] = {"prop_ref": reference_prompt}
        props.append(
            {
                "name": name,
                "description": description,
                "visual_prompt": reference_prompt["positive_prompt"] if reference_prompt else visual_prompt,
                "story_function": story_function,
                "asset_spec": asset_spec,
            }
        )
    return props


def _normalize_character_reference_prompts(
    value: Any,
    *,
    fallback: str,
    style_sentence: str | None,
    index: int,
) -> dict[str, dict[str, str]] | None:
    if not isinstance(value, dict):
        return None
    multi_view_fallback = (
        f"{fallback}。16:9 横向多视角角色设定图，同一角色保持完全一致的外观、服装和配色，"
        "在同一张图中依次展示正面全身、四分之三全身、侧面全身和脸部近景，背景简洁。"
    )
    prompt = _normalize_reference_prompt(
        value.get("character_main_ref"),
        fallback=multi_view_fallback,
        style_sentence=style_sentence,
        context=f"Character {index} reference_prompts.character_main_ref",
    )
    return {"character_main_ref": prompt} if prompt else None


def _normalize_reference_prompt(
    value: Any,
    *,
    fallback: str,
    style_sentence: str | None,
    context: str,
) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    positive = str(value.get("positive_prompt") or fallback).strip()
    positive = _normalize_natural_prompt(positive, context=context)
    positive = strip_project_style(positive, style_sentence)
    negative = str(value.get("negative_prompt") or "").strip()
    return {
        "positive_prompt": positive,
        "negative_prompt": negative or "字幕，水印，logo，可读文字",
    }


def _normalize_natural_prompt(value: str, *, context: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    normalized = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    if not normalized:
        raise ValueError(f"{context}.positive_prompt cannot be empty")
    return normalized


def _nested_prompt(value: Any, role: str) -> Any:
    return value.get(role) if isinstance(value, dict) else None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _localized_role_type(value: str | None) -> str | None:
    if not value:
        return None
    mapping = {
        "protagonist": "主角",
        "antagonist": "反派",
        "supporting": "配角",
        "minor": "次要角色",
        "cameo": "客串",
    }
    return mapping.get(value.strip().lower(), value)


def _localized_gender(value: str | None) -> str | None:
    if not value:
        return None
    mapping = {
        "male": "男性",
        "female": "女性",
        "man": "男性",
        "woman": "女性",
        "unknown": "未指定",
        "unspecified": "未指定",
    }
    return mapping.get(value.strip().lower(), value)
