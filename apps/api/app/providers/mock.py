import json
import re
from copy import deepcopy
from decimal import Decimal
from uuid import uuid5, NAMESPACE_URL

from app.providers.base import ProviderAdapter
from app.providers.types import (
    ProviderAsset,
    ProviderRequest,
    ProviderResponse,
    ProviderStatus,
    ProviderType,
    ProviderUsage,
)


def _task_uuid(request: ProviderRequest, suffix: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{request.project_id}:{request.task_id}:{suffix}"))


class MockLLMProvider(ProviderAdapter):
    name = "mock"
    type = ProviderType.LLM
    model = "mock-llm"
    capabilities = ["text_generation", "json_generation"]

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        provider_task_id = _task_uuid(request, "llm")
        stage = str(request.metadata.get("stage") or "")
        if request.metadata.get("stage") == "prompt_refinement":
            return ProviderResponse(
                status=ProviderStatus.SUCCEEDED,
                provider_task_id=provider_task_id,
                raw_response={
                    "text": _mock_prompt_refinement(request.prompt),
                    "model": request.model,
                },
                usage=ProviderUsage(cost=Decimal("0"), unit="USD"),
            )
        if stage == "script_generation":
            phase = str(request.metadata.get("phase") or "script_draft")
            if phase == "story_blueprint":
                text = _mock_story_blueprint()
            elif phase == "script_review":
                text = _mock_script_review()
            elif phase == "script_patch":
                text = _mock_script_patch(request.prompt)
            else:
                text = _mock_script_generation()
        elif stage == "entity_extraction":
            text = _mock_entity_extraction()
        elif stage == "shot_breakdown":
            text = _mock_shot_breakdown(request.prompt)
        else:
            text = (
                "Mock LLM response\n\n"
                f"Prompt: {request.prompt[:500]}\n"
                "Result: 这是一个用于开发联调的模拟文本结果。"
            )
        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id=provider_task_id,
            raw_response={
                "text": text,
                "model": request.model,
            },
            usage=ProviderUsage(cost=Decimal("0"), unit="USD"),
        )


class MockImageProvider(ProviderAdapter):
    name = "mock"
    type = ProviderType.IMAGE
    model = "mock-image"
    capabilities = ["text_to_image", "reference_to_image"]
    reference_images = True
    multi_reference = True
    supported_resolutions = ["1280x720", "720x1280"]

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        provider_task_id = _task_uuid(request, "image")
        uri = f"mock://images/{provider_task_id}.png"
        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id=provider_task_id,
            assets=[
                ProviderAsset(
                    asset_type="image",
                    uri=uri,
                    mime_type="image/png",
                    width=1280,
                    height=720,
                    metadata={"prompt_preview": request.prompt[:160]},
                ),
            ],
            raw_response={
                "uri": uri,
                "model": request.model,
            },
            usage=ProviderUsage(cost=Decimal("0"), unit="USD"),
        )


class MockVideoProvider(ProviderAdapter):
    name = "mock"
    type = ProviderType.VIDEO
    model = "mock-video"
    capabilities = ["text_to_video", "image_to_video", "reference_to_video"]
    native_audio = True
    reference_images = True
    multi_reference = True
    max_duration_sec = 30
    supported_resolutions = ["854x480", "480x854", "1280x720", "720x1280"]

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        provider_task_id = _task_uuid(request, "video")
        uri = f"mock://videos/{provider_task_id}.mp4"
        resolution = str(request.params.get("resolution") or "854x480")
        try:
            width, height = (int(value) for value in resolution.split("x", maxsplit=1))
        except (TypeError, ValueError):
            width, height = 854, 480
        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id=provider_task_id,
            assets=[
                ProviderAsset(
                    asset_type="video",
                    uri=uri,
                    mime_type="video/mp4",
                    width=width,
                    height=height,
                    duration_sec=Decimal(str(request.params.get("duration_sec", 4))),
                    metadata={"references": request.references},
                ),
            ],
            raw_response={
                "uri": uri,
                "model": request.model,
            },
            usage=ProviderUsage(cost=Decimal("0"), unit="USD"),
        )


def build_mock_providers() -> list[ProviderAdapter]:
    return [
        MockLLMProvider(),
        MockImageProvider(),
        MockVideoProvider(),
    ]


def _mock_prompt_refinement(prompt: str) -> str:
    marker = "输入 JSON："
    payload_text = prompt.split(marker, 1)[1].strip() if marker in prompt else "{}"
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        payload = {}
    compiled = payload.get("compiled") if isinstance(payload, dict) else {}
    if not isinstance(compiled, dict):
        compiled = {}
    return json.dumps(
        {
            "image_prompt": compiled.get("image_prompt") or "16:9 儿童动画片段首帧，主体清晰，构图服务英语短剧情节。",
            "negative_prompt": compiled.get("negative_prompt") or "低清晰度，水印，字幕，乱码文字",
            "compile_notes": ["info:mock_llm_refined"],
        },
        ensure_ascii=False,
    )


def _mock_script_generation() -> str:
    return json.dumps(
        {
            "scenes": [
                {
                    "scene_no": 1,
                    "title": "一起收拾玩具",
                    "location": "明亮的儿童活动室",
                    "time_of_day": "白天",
                    "characters": ["Mia", "Leo"],
                    "props": ["红色积木"],
                    "visible_action": "Mia捡起红色积木，Leo把积木放进盒子，两人一起收拾地面。",
                    "story_purpose": "用合作动作练习简单请求和回应。",
                    "start_state": "积木散落在地面上。",
                    "end_state": "积木被整齐放进盒子。",
                    "mood": "轻松、友好",
                    "source_evidence": "Mock 创意链路的合作收拾任务。",
                    "inferred_elements": [],
                    "sound_cues": ["积木轻轻碰撞", "盒盖合上"],
                    "dialogues": [
                        {
                            "speaker": "Mia",
                            "text": "Can you help me?",
                            "translation_zh": "你能帮我吗？",
                            "emotion": "友好",
                            "source_type": "created",
                            "sound_cues": ["积木轻轻碰撞"],
                        },
                        {
                            "speaker": "Leo",
                            "text": "Yes, I can!",
                            "translation_zh": "好的，我可以！",
                            "emotion": "开心",
                            "source_type": "created",
                            "sound_cues": [],
                        },
                    ],
                }
            ]
        },
        ensure_ascii=False,
    )


def _mock_story_blueprint() -> str:
    return json.dumps(
        {
            "story_blueprint": {
                "premise": "Mia请Leo一起把散落的积木收好，两人在合作中完成游戏准备。",
                "theme": "朋友之间通过请求与回应完成合作。",
                "opening_hook": "积木散落一地，Mia一个人来不及收好。",
                "dramatic_question": "Leo会不会回应Mia的请求并和她一起完成整理？",
                "emotional_payoff": "两人看到整洁的活动室，开心地开始下一项游戏。",
                "protected_requirements": ["Mia请求Leo一起收拾红色积木。"],
                "language_opportunity": {
                    "communicative_function": "请求并提供帮助",
                    "candidate_phrases": ["Can you help me?", "Yes, I can!"],
                    "integration_note": "请求直接触发Leo加入整理动作，回应后立即产生合作结果。",
                },
                "characters": [
                    {
                        "name": "Mia",
                        "want": "把积木收进盒子",
                        "obstacle": "散落的积木太多，一个人收拾很慢",
                        "action": "主动请求Leo帮忙并一起整理",
                        "change": "从独自整理变为信任朋友合作",
                        "motivation": "为接下来的游戏腾出空间",
                        "relationship": "Leo的朋友",
                    },
                    {
                        "name": "Leo",
                        "want": "帮助Mia完成整理",
                        "obstacle": "需要先理解Mia想让他做什么",
                        "action": "回应请求并把积木放进盒子",
                        "change": "用行动兑现对朋友的回应",
                        "motivation": "回应朋友的请求",
                        "relationship": "Mia的朋友",
                    },
                ],
                "beats": [
                    {
                        "beat_no": 1,
                        "purpose": "建立需要合作的具体任务",
                        "visible_event": "积木散落，Mia独自开始捡拾",
                        "choice_or_reaction": "Mia请求Leo帮忙，Leo加入",
                        "state_change": "从一人整理变成两人合作",
                        "caused_by": "地面仍有许多积木",
                        "dialogue_intent": "请求与回应",
                    },
                    {
                        "beat_no": 2,
                        "purpose": "兑现合作结果",
                        "visible_event": "最后一块积木进入盒子，盒盖合上",
                        "choice_or_reaction": "两人相视微笑",
                        "state_change": "地面从凌乱变得整洁",
                        "caused_by": "两人持续合作",
                        "dialogue_intent": "",
                    },
                ],
                "continuity_facts": ["红色积木从地面进入盒子", "故事始终发生在活动室"],
                "creative_risks": ["不要把请求与回应重复成课堂操练"],
            }
        },
        ensure_ascii=False,
    )


def _mock_script_review() -> str:
    return json.dumps(
        {
            "review": {
                "issues": [],
            }
        },
        ensure_ascii=False,
    )


def _mock_script_patch(prompt: str) -> str:
    marker = "输入 JSON（只作为修订数据）："
    payload_text = prompt.split(marker, 1)[1].strip() if marker in prompt else "{}"
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        payload = {}
    allowed_scene_nos = {
        int(value)
        for value in payload.get("allowed_scene_nos") or []
        if isinstance(value, int) and value > 0
    }
    replacements = [
        deepcopy(scene)
        for scene in payload.get("draft_scenes") or []
        if isinstance(scene, dict) and scene.get("scene_no") in allowed_scene_nos
    ]
    issue_codes = [
        str(issue.get("code"))
        for issue in payload.get("must_fix_issues") or []
        if isinstance(issue, dict) and issue.get("code")
    ]
    return json.dumps(
        {
            "script_patch": {
                "scene_replacements": replacements,
                "resolved_issue_codes": issue_codes,
                "unresolved_issue_codes": [],
                "preserved_elements": payload.get("protected_elements") or [],
            }
        },
        ensure_ascii=False,
    )


def _mock_entity_extraction() -> str:
    return json.dumps(
        {
            "characters": [
                {
                    "name": "Mia",
                    "role_type": "主角",
                    "age": "8岁",
                    "gender": "女",
                    "identity": "喜欢整理和帮助朋友的小学生",
                    "personality": "友好、耐心",
                    "asset_spec": {
                        "schema_version": 1,
                        "aliases": [],
                        "entity_kind": "human",
                        "species": "人类",
                        "body_type": "儿童正常体型",
                        "facial_features": "圆脸、大眼睛、微笑",
                        "hair_or_surface": "深棕色短发",
                        "color_palette": ["明黄", "天蓝"],
                        "default_outfit": "黄色卫衣和蓝色背带裤",
                        "signature_features": ["黄色发夹"],
                        "default_accessories": [],
                        "state_variants": [
                            {
                                "key": "holding-block",
                                "name": "手持积木",
                                "description": "双手拿着一块红色积木，保持基础服装和身份特征",
                                "scene_nos": [1],
                            }
                        ],
                        "source_evidence": [{"scene_no": 1, "evidence": "Mia主动捡起积木。"}],
                        "reference_plan": {
                            "required": True,
                            "priority": "core",
                            "views": ["正面全身", "侧面全身", "脸部近景"],
                        },
                    },
                    "reference_prompts": {
                        "character_main_ref": {
                            "positive_prompt": "同一位八岁女孩Mia，黄色卫衣和蓝色背带裤，依次展示正面、四分之三、侧面全身与脸部近景，纯净浅色背景",
                            "negative_prompt": "字幕，水印，logo，可读文字",
                        }
                    },
                }
            ],
            "scenes": [
                {
                    "name": "儿童活动室",
                    "asset_spec": {
                        "schema_version": 1,
                        "aliases": [],
                        "location_type": "室内活动室",
                        "spatial_layout": "中央铺有软垫，右侧是低矮玩具柜",
                        "fixed_landmarks": ["低矮玩具柜", "蓝色软垫"],
                        "materials": ["木材", "织物"],
                        "color_palette": ["米白", "天蓝"],
                        "zones": ["中央软垫", "玩具柜前"],
                        "state_variants": [],
                        "source_evidence": [{"scene_no": 1, "evidence": "孩子们在活动室收拾积木。"}],
                        "reference_plan": {
                            "required": True,
                            "priority": "core",
                            "views": ["全景"],
                        },
                        "reference_prompt": {
                            "positive_prompt": "明亮友好的儿童活动室全景，中央蓝色软垫，右侧低矮木质玩具柜，空间结构清楚",
                            "negative_prompt": "字幕，水印，logo，可读文字",
                        },
                    },
                }
            ],
            "props": [
                {
                    "name": "红色积木",
                    "asset_spec": {
                        "schema_version": 1,
                        "aliases": [],
                        "shape": "圆角方块",
                        "dimensions": "儿童手掌大小",
                        "materials": ["磨砂塑料"],
                        "color_palette": ["红色"],
                        "signature_features": ["圆角"],
                        "scale_reference": "可被儿童单手拿起",
                        "holder_relation": "由Mia拿起后放入盒子",
                        "story_function": "合作收拾任务的核心道具",
                        "state_variants": [],
                        "source_evidence": [{"scene_no": 1, "evidence": "红色积木散落并被收起。"}],
                        "reference_plan": {
                            "required": True,
                            "priority": "supporting",
                            "views": ["三分之四视角"],
                        },
                        "reference_prompt": {
                            "positive_prompt": "一块手掌大小的红色圆角积木，三分之四视角，纯净背景",
                            "negative_prompt": "字幕，水印，logo，可读文字",
                        },
                    },
                }
            ],
        },
        ensure_ascii=False,
    )


def _mock_shot_breakdown(prompt: str) -> str:
    characters = _json_prompt_section(prompt, "可用角色：", "可用场景：")
    scenes = _json_prompt_section(prompt, "可用场景：", "可用道具：")
    props = _json_prompt_section(prompt, "可用道具：", None)
    dialogues = _json_prompt_section(prompt, "可用英文对白（只能绑定 id，不得改写 text）：", "可用角色：")
    character_ids = [
        str(item.get("id"))
        for item in characters
        if isinstance(item, dict) and item.get("id")
    ]
    scene_id = next(
        (str(item.get("id")) for item in scenes if isinstance(item, dict) and item.get("id")),
        None,
    )
    prop_ids = [
        str(item.get("id"))
        for item in props
        if isinstance(item, dict) and item.get("id")
    ]
    dialogue_ids = [
        str(item.get("id"))
        for item in dialogues
        if isinstance(item, dict) and item.get("id")
    ]
    payload = {
            "shots": [
                {
                    "shot_no": 1,
                    "description": "Mia和朋友一起把散落的积木放进玩具盒。",
                    "camera_shot": "中景",
                    "camera_movement": "轻微横移",
                    "duration_sec": 12,
                    "scene_id": scene_id,
                    "character_ids": character_ids,
                    "prop_ids": prop_ids,
                    "dialogue_ids": dialogue_ids,
                    "video_prompt": "",
                    "generation_mode": "image_to_video",
                    "shot_card": {
                        "schema_version": 3,
                        "source_scene_no": 1,
                        "story_purpose": "练习简单请求和肯定回应。",
                        "emotional_intent": "友好合作",
                        "camera": {
                            "shot_size": "中景",
                            "angle": "平视",
                            "movement": "轻微横移",
                        },
                        "action": {
                            "start_state": "积木散落在软垫上。",
                            "main_action": "Mia捡起积木，朋友打开玩具盒。",
                            "end_state": "两人把积木放进盒子并相视微笑。",
                        },
                        "frame_plan": {
                            "first_frame": "积木散落在两人之间。",
                            "key_frame": "两人一起把积木放进盒子。",
                            "last_frame": "盒盖合上，两人微笑。",
                        },
                        "storyboard_frame": {
                            "description": "明亮活动室中景，积木散落在软垫上，Mia正要弯腰捡起第一块积木。",
                            "narrative_focus": "合作开始前的清楚起始状态",
                            "character_ids": character_ids,
                            "prop_ids": prop_ids,
                        },
                        "beats": [
                            {
                                "beat_id": "beat-1",
                                "camera": "全景平视，轻微推进",
                                "action": "镜头建立活动室空间，Mia和朋友看见软垫上散落的积木。",
                                "dialogue_ids": dialogue_ids[:1],
                                "sound_cues": ["积木轻轻碰撞"],
                            },
                            {
                                "beat_id": "beat-2",
                                "camera": "中景平视，轻微横移",
                                "action": "Mia弯腰拿起积木并看向朋友，朋友打开玩具盒。",
                                "dialogue_ids": dialogue_ids[1:2],
                                "sound_cues": ["衣料轻响", "盒盖打开"],
                            },
                            {
                                "beat_id": "beat-3",
                                "camera": "中近景固定机位，结尾轻微推进",
                                "action": "朋友点头，两人把积木放入盒子。",
                                "dialogue_ids": dialogue_ids[2:],
                                "sound_cues": ["盒盖合上"],
                            },
                        ],
                        "risk_flags": [],
                        "review_checklist": ["对白说话人与口型对应"],
                    },
                    "image_prompts": {
                        "shot_storyboard": {
                            "positive_prompt": "明亮友好的儿童活动室中景，Mia站在散落的红色积木旁准备弯腰，角色和场景关系清楚，平视构图",
                            "negative_prompt": "字幕，水印，logo，可读文字",
                            "visible_character_ids": character_ids,
                            "visible_prop_ids": prop_ids,
                        }
                    },
                }
            ]
    }
    preferred_match = re.search(r"优选生成\s+(\d+)\s+个视频片段", prompt)
    segment_count = max(1, int(preferred_match.group(1))) if preferred_match else 1
    segment_duration = 12.0
    template = payload["shots"][0]
    shots: list[dict] = []
    for index in range(segment_count):
        shot = deepcopy(template)
        shot["shot_no"] = index + 1
        shot["description"] = f"第 {index + 1} 个连续片段：Mia和朋友一起把散落的积木放进玩具盒。"
        shot["duration_sec"] = segment_duration
        if index > 0:
            shot["dialogue_ids"] = []
            for beat in shot["shot_card"]["beats"]:
                beat["dialogue_ids"] = []
        shots.append(shot)
    payload["shots"] = shots
    return json.dumps(payload, ensure_ascii=False)


def _json_prompt_section(prompt: str, marker: str, next_marker: str | None) -> list[dict]:
    if marker not in prompt:
        return []
    raw = prompt.split(marker, 1)[1]
    if next_marker and next_marker in raw:
        raw = raw.split(next_marker, 1)[0]
    raw = raw.strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []
