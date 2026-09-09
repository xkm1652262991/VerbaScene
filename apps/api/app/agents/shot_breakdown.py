import json
import re
from decimal import Decimal, InvalidOperation
from math import ceil
from typing import Any

from app.agents.camera_language import professional_movement, professional_shot_size
from app.agents.shot_timing import strip_internal_timing
from app.agents.style import project_style_prompt_line, strip_project_style
from app.models import Character, Prop, Scene, Script


IMAGE_PROMPT_KEYS = ("shot_storyboard",)
SEGMENT_MIN_DURATION_SEC = 4
SEGMENT_MAX_DURATION_SEC = 15
TARGET_DURATION_TOLERANCE_RATIO = 0.15
STRUCTURAL_EVENT_COUNT_FLOOR_SEC = 8
SEGMENT_MIN_INTERNAL_SHOTS = 2
SEGMENT_MAX_INTERNAL_SHOTS = 4


def build_shot_breakdown_prompt(
    *,
    script: Script,
    characters: list[Character],
    scenes: list[Scene],
    props: list[Prop],
    style: str,
    target_duration_sec: int | float | Decimal = 90,
) -> str:
    segment_contract = segment_planning_contract(target_duration_sec)
    return f"""
你是儿童动画短剧导演。一次完成“可独立生成的视频片段”拆解、内部镜头节拍和唯一分镜首帧 Prompt。

只输出 JSON：{{"shots": [...]}}。
项目目标总时长约 {segment_contract["target_duration_sec"]:.0f} 秒；全部片段规划总时长必须在
{segment_contract["total_duration_range"]["min_sec"]:.0f}-{segment_contract["total_duration_range"]["max_sec"]:.0f} 秒之间。
单个片段总时长必须是 {segment_contract["shot_duration_range"]["min_sec"]:.0f}-{segment_contract["shot_duration_range"]["max_sec"]:.0f} 之间的整数秒。
片段数量的硬范围为 {segment_contract["min_segment_count"]}-{segment_contract["max_segment_count"]} 个；
实际数量由剧情事件决定，不存在优选平均片段数。
scene_id、character_ids、prop_ids 和 dialogue_ids 只能使用给定 id。
一个 shot 是一次视频模型调用，不是单个摄影镜头，并包含
{SEGMENT_MIN_INTERNAL_SHOTS}-{SEGMENT_MAX_INTERNAL_SHOTS} 个按剧情顺序发生的内部摄影镜头 beats。
先识别剧情事件，再根据事件功能、动作复杂度、对白承载量和情绪变化为每个 shot 分配一个总时长；
不得先计算平均秒数再机械切片。不得为任何 beat 分配秒数。

分片规则（最高优先级）：
- 普通景别变化、机位变化、对话轮次、动作与反应不得单独创建 2-3 秒 shot，应写入同一 shot 的多个 beats。
- 只有场景或时空变化、叙事连续性断裂、主要角色/场景/道具集合明显变化，或一次生成无法自然承载完整动作时，才创建下一个 shot。
- 同一 shot 内保持同一场景与时间连续，但允许有动机的切镜、景别变化和机位变化。
- 不得为了增加卡片数量重复剧情，不得把一个完整对白动作拆成多个短 shot。

输出语言（最高优先级）：
- 除人物等必须保留的专有名称，以及 JSON 字段名、id 和约定枚举值外，所有面向用户和图片/视频模型的内容必须使用自然、完整的简体中文。
- 上游剧本或资产中含有英文或其他语言时，先理解并翻译其含义；不得把外文地点、物品、动作、情绪、构图或完整描述句直接复制到输出。
- description、camera_shot、camera_movement、shot_card 中的叙事目的、情绪、动作、帧描述、节奏说明、音效、风险与审核项，以及全部 positive_prompt、negative_prompt 都使用简体中文。
- 英文 Dialogue 原文不得翻译成中文；只通过 dialogue_ids 绑定，由后端 Prompt 编译器精确注入。
- 人物专名如 Mia Rabbit 可以保留原文；普通名词和描述必须中文化，例如 kitchen 写成“厨房”、cookie jar 写成“饼干罐”、shocked 写成“震惊”。

每个 shot 必须包含：
- shot_no、description、camera_shot、camera_movement
- duration_sec：Agent 规划的片段总时长，只能是整数秒
- scene_id、character_ids、prop_ids、dialogue_ids
- video_prompt（允许空字符串）、generation_mode（固定 image_to_video）
- shot_card
- image_prompts

shot_card 保存视频片段和审核需要的结构：
- schema_version（固定 3）
- source_scene_no
- story_purpose、emotional_intent
- duration_rationale：说明该事件为什么需要这个片段总时长，不得拆成 beat 时间表
- camera: shot_size、angle、movement
- action: start_state、main_action、end_state
- frame_plan: first_frame、key_frame、last_frame
- storyboard_frame: description、narrative_focus、character_ids、prop_ids
- beats: array，每项必须包含 beat_id、camera、action、dialogue_ids、sound_cues
- segment_plan 由后端根据 Provider 能力写入，模型不要输出
- motion_timing: motion_complexity、beats（兼容镜像，不得包含时长依据或时间点）
- risk_flags、review_checklist

每个 beat：
- camera 使用简体中文镜头语言，每个 beat 只使用一种主要运镜，不在同一镜头叠加推、拉、摇、移。
- action 是可见动作，具体写明身体部位、动作幅度/速度/力度和前后动作的过渡或惯性。
- 抽象情绪必须外化为眼神、眉眼、嘴角、肩膀、手部或身体停顿等可见表现。
- dialogue_ids 绑定本节拍发生的英文对白。
- sound_cues 是环境、动作或拟声音效提示。
- 一个 dialogue_id 只能绑定一次。
- 只表达剧情先后和镜头推进，不输出 duration_sec、时间区间、时间戳或“第几秒”。

storyboard_frame 是片段的单张分镜预览与可选首帧候选，必须与 action.start_state 和 frame_plan.first_frame 表达同一个起始瞬间。
它不会自动成为视频输入；只有用户明确启用“使用首帧”时才作为首帧发送。画面只能表现动作发生前或刚开始时的静态状态，不得提前画出主动作结果、剧情高潮或镜头结束状态。
frame_plan 仅用于规划视频动作过程，不再生成独立首帧、关键帧或尾帧图片。

image_prompts 只能包含 shot_storyboard。该项包含：
- positive_prompt
- negative_prompt
- visible_character_ids
- visible_prop_ids

shot_storyboard.positive_prompt 是供系统组合的中文主体 Prompt，具体写清镜头起始瞬间的：
主体外观、姿态动作、表情视线、关键道具、空间关系、视觉焦点、构图机位和场景光线。
不得复制或改写项目视觉风格；系统会在图片 Provider 调用时统一注入当前风格。
negative_prompt 默认只写“字幕，水印，logo，可读文字”。
visible_character_ids 和 visible_prop_ids 是选择参考资产的唯一依据，只能使用本镜头绑定的 id。

镜头语言使用中文专业术语。先为一次连续生成设计完整的起承转合，再把它按剧情顺序组织成 2-4 个内部镜头；
短反应、对白和小幅动作可以占据一个内部镜头，但不得因此拆成新的 shot。

{project_style_prompt_line(style)}

已确认剧本：
{script.content}

结构化生产剧本：
{json.dumps(script.scenes if isinstance(script.scenes, list) else [], ensure_ascii=False, indent=2)}

可用英文对白（只能绑定 id，不得改写 text）：
    {json.dumps(getattr(script, "dialogues", []) if isinstance(getattr(script, "dialogues", []), list) else [], ensure_ascii=False, indent=2)}

可用角色：
{_entity_json(characters, ("id", "name", "identity", "appearance", "asset_spec"))}

可用场景：
{_entity_json(scenes, ("id", "name", "description", "asset_spec"))}

可用道具：
{_entity_json(props, ("id", "name", "description", "story_function", "asset_spec"))}
""".strip()


def parse_shot_breakdown_response(
    *,
    text: str,
    characters: list[Character],
    scenes: list[Scene],
    props: list[Prop],
    style_sentence: str | None = None,
    allowed_dialogue_ids: set[str] | None = None,
    segment_contract: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    payload = _json_payload(text)
    raw_shots = payload.get("shots") if isinstance(payload, dict) else payload
    if not isinstance(raw_shots, list):
        raise ValueError("LLM response must contain non-empty shots array")
    raw_shots = [item for item in raw_shots if isinstance(item, dict)]
    if not raw_shots:
        raise ValueError("LLM response must contain non-empty shots array")

    allowed_characters = {item.id for item in characters}
    allowed_scenes = {item.id for item in scenes}
    allowed_props = {item.id for item in props}
    resolved_dialogue_ids = (
        allowed_dialogue_ids
        if allowed_dialogue_ids is not None
        else _collect_dialogue_ids(raw_shots)
    )
    shots = [
        _normalize_shot(
            raw,
            index=index,
            allowed_characters=allowed_characters,
            allowed_scenes=allowed_scenes,
            allowed_props=allowed_props,
            allowed_dialogues=resolved_dialogue_ids,
            style_sentence=(style_sentence or "").strip(),
        )
        for index, raw in enumerate(raw_shots, start=1)
    ]
    if segment_contract is not None:
        _validate_segment_contract(shots, segment_contract)
    return shots


def _normalize_shot(
    raw: Any,
    *,
    index: int,
    allowed_characters: set[str],
    allowed_scenes: set[str],
    allowed_props: set[str],
    allowed_dialogues: set[str],
    style_sentence: str,
) -> dict[str, Any]:
    scene_id = raw.get("scene_id")
    if scene_id is not None and scene_id not in allowed_scenes:
        scene_id = None
    character_ids = _ids(raw.get("character_ids"), allowed_characters)
    prop_ids = _ids(raw.get("prop_ids"), allowed_props)
    description = _text(raw.get("description")) or f"镜头 {index}"
    raw_shot_card = raw.get("shot_card") if isinstance(raw.get("shot_card"), dict) else {}
    raw_dialogue_ids = _ids(raw.get("dialogue_ids"), allowed_dialogues)
    shot_card = _shot_card(
        raw_shot_card,
        description,
        character_ids,
        prop_ids,
        allowed_dialogues=allowed_dialogues,
        fallback_dialogue_ids=raw_dialogue_ids,
    )
    if not shot_card["duration_rationale"]:
        shot_card["duration_rationale"] = _optional_text(raw.get("duration_rationale"))
    duration_sec = _duration_seconds(raw.get("duration_sec"))
    dialogue_ids = list(
        dict.fromkeys(
            dialogue_id
            for beat in shot_card["beats"]
            for dialogue_id in beat["dialogue_ids"]
        )
    )
    image_prompts = _image_prompts(
        raw.get("image_prompts") or raw_shot_card.get("image_prompts"),
        shot_card=shot_card,
        character_ids=character_ids,
        prop_ids=prop_ids,
        style_sentence=style_sentence,
    )
    storyboard = image_prompts["shot_storyboard"]
    return {
        "shot_no": _positive_int(raw.get("shot_no"), index),
        "description": description,
        "camera_shot": professional_shot_size(_text(raw.get("camera_shot")) or "中景"),
        "camera_movement": professional_movement(_text(raw.get("camera_movement")) or "固定机位"),
        "duration_sec": duration_sec,
        "scene_id": scene_id,
        "character_ids": character_ids,
        "prop_ids": prop_ids,
        "dialogue_ids": dialogue_ids,
        "shot_card": {
            **shot_card,
            "segment_plan": _segment_plan(
                duration_sec,
                duration_rationale=shot_card["duration_rationale"],
                internal_shot_count=len(shot_card["beats"]),
            ),
            "image_prompts": image_prompts,
        },
        "image_prompts": image_prompts,
        "image_prompt": storyboard["positive_prompt"],
        "negative_prompt": storyboard["negative_prompt"],
        "video_prompt": _optional_text(raw.get("video_prompt")),
        "generation_mode": "image_to_video",
    }


def _shot_card(
    value: Any,
    description: str,
    character_ids: list[str],
    prop_ids: list[str],
    *,
    allowed_dialogues: set[str],
    fallback_dialogue_ids: list[str],
) -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    action_value = value.get("action") if isinstance(value.get("action"), dict) else {}
    action = {
        "start_state": strip_internal_timing(action_value.get("start_state")) or description,
        "main_action": strip_internal_timing(action_value.get("main_action")) or description,
        "end_state": strip_internal_timing(action_value.get("end_state")) or description,
    }
    frame_value = value.get("frame_plan") if isinstance(value.get("frame_plan"), dict) else {}
    frames = {
        "first_frame": _text(frame_value.get("first_frame")) or action["start_state"],
        "key_frame": _text(frame_value.get("key_frame")) or action["main_action"],
        "last_frame": _text(frame_value.get("last_frame")) or action["end_state"],
    }

    storyboard = value.get("storyboard_frame") if isinstance(value.get("storyboard_frame"), dict) else {}
    normalized_storyboard = {
        "description": _text(storyboard.get("description")) or frames["first_frame"],
        "narrative_focus": _text(storyboard.get("narrative_focus"))
        or _text(value.get("story_purpose"))
        or description,
        "character_ids": _ids(storyboard.get("character_ids"), set(character_ids), character_ids),
        "prop_ids": _ids(storyboard.get("prop_ids"), set(prop_ids), prop_ids),
    }
    camera = value.get("camera") if isinstance(value.get("camera"), dict) else {}
    timing = value.get("motion_timing") if isinstance(value.get("motion_timing"), dict) else {}
    beats = _beats(
        value.get("beats") or timing.get("beats"),
        allowed_dialogues=allowed_dialogues,
        fallback_action=action["main_action"],
        fallback_camera=camera,
        fallback_dialogue_ids=fallback_dialogue_ids,
    )
    return {
        "schema_version": 3,
        "source_scene_no": value.get("source_scene_no"),
        "story_purpose": _optional_text(value.get("story_purpose")),
        "emotional_intent": _optional_text(value.get("emotional_intent")),
        "duration_rationale": _optional_text(value.get("duration_rationale")),
        "camera": camera,
        "action": action,
        "frame_plan": frames,
        "storyboard_frame": normalized_storyboard,
        "beats": beats,
        "motion_timing": {
            "motion_complexity": _optional_text(timing.get("motion_complexity")),
            "beats": beats,
        },
        "risk_flags": _string_list(value.get("risk_flags")),
        "review_checklist": _string_list(value.get("review_checklist")),
    }


def _beats(
    value: Any,
    *,
    allowed_dialogues: set[str],
    fallback_action: str,
    fallback_camera: dict[str, Any],
    fallback_dialogue_ids: list[str],
) -> list[dict[str, Any]]:
    raw_items = value if isinstance(value, list) else []
    beats: list[dict[str, Any]] = []
    used_dialogue_ids: set[str] = set()
    for index, item in enumerate(raw_items, start=1):
        source = item if isinstance(item, dict) else {}
        dialogue_ids = [
            dialogue_id
            for dialogue_id in _ids(source.get("dialogue_ids"), allowed_dialogues)
            if dialogue_id not in used_dialogue_ids
        ]
        used_dialogue_ids.update(dialogue_ids)
        beats.append(
            {
                "beat_id": _text(source.get("beat_id")) or f"beat-{index}",
                "camera": strip_internal_timing(source.get("camera"))
                or strip_internal_timing(source.get("camera_language"))
                or "，".join(str(item) for item in fallback_camera.values() if str(item).strip()),
                "action": strip_internal_timing(source.get("action"))
                or strip_internal_timing(source.get("description"))
                or strip_internal_timing(fallback_action),
                "dialogue_ids": dialogue_ids,
                "sound_cues": _string_list(source.get("sound_cues")),
            }
        )
    if beats:
        return beats
    return [
        {
            "beat_id": "beat-1",
            "camera": "，".join(str(item) for item in fallback_camera.values() if str(item).strip()),
            "action": fallback_action,
            "dialogue_ids": fallback_dialogue_ids,
            "sound_cues": [],
        }
    ]


def _image_prompts(
    value: Any,
    *,
    shot_card: dict[str, Any],
    character_ids: list[str],
    prop_ids: list[str],
    style_sentence: str,
) -> dict[str, dict[str, Any]]:
    value = value if isinstance(value, dict) else {}
    fallback_prompts = {
        "shot_storyboard": shot_card["storyboard_frame"]["description"]
        or shot_card["frame_plan"]["first_frame"],
    }
    result: dict[str, dict[str, Any]] = {}
    for key in IMAGE_PROMPT_KEYS:
        item = value.get(key)
        item = item if isinstance(item, dict) else {}
        positive = _text(item.get("positive_prompt")) or fallback_prompts[key]
        positive = strip_project_style(positive, style_sentence)
        result[key] = {
            "positive_prompt": positive,
            "negative_prompt": _optional_text(item.get("negative_prompt")) or "字幕，水印，logo，可读文字",
            "visible_character_ids": _ids(item.get("visible_character_ids"), set(character_ids), character_ids),
            "visible_prop_ids": _ids(item.get("visible_prop_ids"), set(prop_ids), prop_ids),
        }
    return result


def _entity_json(items: list[Any], fields: tuple[str, ...]) -> str:
    return json.dumps(
        [{field: getattr(item, field) for field in fields if getattr(item, field, None) is not None} for item in items],
        ensure_ascii=False,
        indent=2,
    )


def _json_payload(text: str) -> dict[str, Any] | list[Any]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    if not cleaned.startswith("{"):
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    payload = json.loads(cleaned)
    if not isinstance(payload, (dict, list)):
        raise ValueError("LLM response JSON must contain shots")
    return payload


def _text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return "\n".join(
        re.sub(r"[ \t]+", " ", line).strip()
        for line in value.replace("\r", "").split("\n")
    ).strip()


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _ids(value: Any, allowed: set[str], fallback: list[str] | None = None) -> list[str]:
    if not isinstance(value, list):
        return list(fallback or [])
    ids = list(dict.fromkeys(item for item in value if isinstance(item, str)))
    return [item for item in ids if item in allowed]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _collect_dialogue_ids(raw_shots: list[dict[str, Any]]) -> set[str]:
    result: set[str] = set()
    for shot in raw_shots:
        result.update(_string_list(shot.get("dialogue_ids")))
        card = shot.get("shot_card") if isinstance(shot.get("shot_card"), dict) else {}
        timing = card.get("motion_timing") if isinstance(card.get("motion_timing"), dict) else {}
        beats = card.get("beats") or timing.get("beats")
        if not isinstance(beats, list):
            continue
        for beat in beats:
            if isinstance(beat, dict):
                result.update(_string_list(beat.get("dialogue_ids")))
    return result


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _duration_seconds(value: Any) -> int | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0 or not parsed.is_integer():
        return None
    return int(parsed)


def _segment_plan(
    duration_sec: int | None,
    *,
    duration_rationale: str | None,
    internal_shot_count: int,
) -> dict[str, Any]:
    if duration_sec is None:
        return {
            "duration_mode": "provider_auto",
            "duration_source": "legacy_or_manual",
            "planned_duration_sec": None,
            "duration_rationale": duration_rationale,
            "expected_duration_range": {
                "min_sec": SEGMENT_MIN_DURATION_SEC,
                "max_sec": SEGMENT_MAX_DURATION_SEC,
            },
            "actual_duration_sec": None,
            "internal_shot_count": internal_shot_count,
        }
    return {
        "duration_mode": "fixed",
        "duration_source": "agent",
        "planned_duration_sec": duration_sec,
        "duration_rationale": duration_rationale,
        "expected_duration_range": {
            "min_sec": SEGMENT_MIN_DURATION_SEC,
            "max_sec": SEGMENT_MAX_DURATION_SEC,
        },
        "actual_duration_sec": None,
        "internal_shot_count": internal_shot_count,
    }


def segment_planning_contract(target_duration_sec: int | float | Decimal) -> dict[str, Any]:
    try:
        target = max(1.0, float(target_duration_sec))
    except (TypeError, ValueError, InvalidOperation):
        target = 90.0
    tolerance = max(float(SEGMENT_MIN_DURATION_SEC), target * TARGET_DURATION_TOLERANCE_RATIO)
    min_total = max(float(SEGMENT_MIN_DURATION_SEC), target - tolerance)
    max_total = max(min_total, target + tolerance)
    min_count = max(1, ceil(min_total / SEGMENT_MAX_DURATION_SEC))
    max_count = max(min_count, ceil(max_total / STRUCTURAL_EVENT_COUNT_FLOOR_SEC))
    return {
        "target_duration_sec": target,
        "duration_mode": "fixed",
        "duration_source": "agent",
        "shot_duration_range": {
            "min_sec": SEGMENT_MIN_DURATION_SEC,
            "max_sec": SEGMENT_MAX_DURATION_SEC,
        },
        "total_duration_range": {
            "min_sec": round(min_total, 2),
            "max_sec": round(max_total, 2),
        },
        "min_internal_shots": SEGMENT_MIN_INTERNAL_SHOTS,
        "max_internal_shots": SEGMENT_MAX_INTERNAL_SHOTS,
        "min_segment_count": min_count,
        "max_segment_count": max_count,
    }


def _validate_segment_contract(
    shots: list[dict[str, Any]],
    contract: dict[str, Any],
) -> None:
    min_internal = int(contract.get("min_internal_shots", SEGMENT_MIN_INTERNAL_SHOTS))
    max_internal = int(contract.get("max_internal_shots", SEGMENT_MAX_INTERNAL_SHOTS))
    min_count = int(contract.get("min_segment_count", 1))
    max_count = int(contract.get("max_segment_count", max(min_count, len(shots))))
    shot_range = contract.get("shot_duration_range") if isinstance(contract.get("shot_duration_range"), dict) else {}
    requires_agent_duration = contract.get("duration_source") == "agent" or bool(shot_range)
    min_duration = float(shot_range.get("min_sec", SEGMENT_MIN_DURATION_SEC))
    max_duration = float(shot_range.get("max_sec", SEGMENT_MAX_DURATION_SEC))
    planned_total = 0.0
    for index, shot in enumerate(shots, start=1):
        beats = (shot.get("shot_card") or {}).get("beats")
        beat_count = len(beats) if isinstance(beats, list) else 0
        if beat_count < min_internal or beat_count > max_internal:
            raise ValueError(
                f"片段 {index} 包含 {beat_count} 个内部镜头，不符合 {min_internal}-{max_internal} 个合同"
            )
        if requires_agent_duration:
            duration_sec = shot.get("duration_sec")
            if duration_sec is None:
                raise ValueError(f"片段 {index} 缺少 Agent 规划的总时长")
            card = shot.get("shot_card") if isinstance(shot.get("shot_card"), dict) else {}
            if not _optional_text(card.get("duration_rationale")):
                raise ValueError(f"片段 {index} 缺少基于剧情事件的时长依据")
            duration = float(duration_sec)
            if duration < min_duration or duration > max_duration:
                raise ValueError(
                    f"片段 {index} 总时长 {duration:g} 秒不符合 {min_duration:g}-{max_duration:g} 秒合同"
                )
            planned_total += duration
    if len(shots) < min_count or len(shots) > max_count:
        raise ValueError(
            f"片段数量 {len(shots)} 不符合目标时长要求的 {min_count}-{max_count} 个范围"
        )
    if requires_agent_duration:
        total_range = contract.get("total_duration_range") if isinstance(contract.get("total_duration_range"), dict) else {}
        min_total = float(total_range.get("min_sec", 0))
        max_total = float(total_range.get("max_sec", float("inf")))
        if planned_total < min_total or planned_total > max_total:
            raise ValueError(
                f"片段规划总时长 {planned_total:g} 秒不符合整集 {min_total:g}-{max_total:g} 秒合同"
            )
