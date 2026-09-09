import json
import unittest
from copy import deepcopy
from types import SimpleNamespace

from app.agents.shot_breakdown import (
    build_shot_breakdown_prompt,
    parse_shot_breakdown_response,
    segment_planning_contract,
)


STYLE = "统一二维童话动画，干净线条，柔和赛璐璐上色"


class DirectShotImagePromptTests(unittest.TestCase):
    def setUp(self):
        self.rabbit = SimpleNamespace(id="mia", name="Mia Rabbit")
        self.scene = SimpleNamespace(id="kitchen", name="厨房")
        self.jar = SimpleNamespace(id="jar", name="饼干罐")

    def _payload(self) -> dict:
        return {
            "shots": [
                {
                    "shot_no": 1,
                    "description": "Mia发现饼干罐空了",
                    "camera_shot": "中景",
                    "camera_movement": "固定机位",
                    "duration_sec": 11,
                    "scene_id": "kitchen",
                    "character_ids": ["mia"],
                    "prop_ids": ["jar"],
                    "dialogue_ids": [],
                    "video_prompt": "",
                    "generation_mode": "image_to_video",
                    "shot_card": {
                        "source_scene_no": 1,
                        "story_purpose": "揭示饼干消失",
                        "emotional_intent": "惊讶",
                        "duration_rationale": "需要完整展示接近、开罐和惊讶反应。",
                        "camera": {"shot_size": "中景", "angle": "平视机位", "movement": "固定机位"},
                        "action": {
                            "start_state": "Mia双手握住罐盖",
                            "main_action": "Mia打开饼干罐并看见空罐底",
                            "end_state": "Mia抱着空罐停住",
                        },
                        "frame_plan": {
                            "first_frame": "Mia双手刚握住罐盖",
                            "key_frame": "罐盖打开，空罐底朝向Mia",
                            "last_frame": "Mia抱着打开的空罐停住",
                        },
                        "storyboard_frame": {
                            "description": "Mia双手刚握住仍然盖着盖子的饼干罐",
                            "narrative_focus": "打开饼干罐前的起始动作",
                            "character_ids": ["mia"],
                            "prop_ids": ["jar"],
                        },
                        "motion_timing": {"motion_complexity": "低", "duration_rationale": "短反应", "beats": []},
                        "risk_flags": [],
                        "review_checklist": [],
                    },
                    "image_prompts": {
                        "shot_storyboard": {
                            "positive_prompt": (
                                "Mia Rabbit 站在厨房台面前，双手刚握住仍然盖着盖子的饼干罐，"
                                f"目光落在罐盖上；中景平视，左侧晨光勾勒她的白色毛发。{STYLE}。"
                            ),
                            "negative_prompt": "字幕，水印，logo，可读文字",
                            "visible_character_ids": ["mia"],
                            "visible_prop_ids": ["jar"],
                        }
                    },
                }
            ]
        }

    def test_prompt_contract_uses_one_storyboard_first_frame(self):
        prompt = build_shot_breakdown_prompt(
            script=SimpleNamespace(content="Mia发现饼干罐空了", scenes=[]),
            characters=[self.rabbit],
            scenes=[self.scene],
            props=[self.jar],
            style=STYLE,
        )

        self.assertIn("storyboard_frame 是片段的单张分镜预览与可选首帧候选", prompt)
        self.assertIn("不会自动成为视频输入", prompt)
        self.assertIn("image_prompts 只能包含 shot_storyboard", prompt)
        self.assertIn("visible_character_ids", prompt)
        self.assertIn("一个 shot 是一次视频模型调用，不是单个摄影镜头", prompt)
        self.assertIn("为每个 shot 分配一个总时长", prompt)
        self.assertIn("不得先计算平均秒数再机械切片", prompt)
        self.assertIn("普通景别变化、机位变化、对话轮次、动作与反应不得单独创建 2-3 秒 shot", prompt)
        self.assertIn("不输出 duration_sec、时间区间、时间戳或“第几秒”", prompt)
        self.assertIn("不得包含时长依据或时间点", prompt)
        self.assertIn("每个 beat 只使用一种主要运镜", prompt)
        self.assertIn("动作幅度/速度/力度", prompt)
        self.assertIn("抽象情绪必须外化", prompt)
        self.assertIn("实际数量由剧情事件决定，不存在优选平均片段数", prompt)
        self.assertNotIn("Prompt Compiler", prompt)

    def test_segment_contract_rejects_short_cards_and_accepts_multi_shot_segment(self):
        payload = self._payload()
        contract = segment_planning_contract(90)

        with self.assertRaisesRegex(ValueError, "2-4 个合同"):
            parse_shot_breakdown_response(
                text=json.dumps(payload, ensure_ascii=False),
                characters=[self.rabbit],
                scenes=[self.scene],
                props=[self.jar],
                style_sentence=STYLE,
                segment_contract=contract,
            )

        payload["shots"][0]["shot_card"]["beats"] = [
            {
                "beat_id": "beat-1",
                "camera": "全景建立厨房空间",
                "action": "Mia走到台面前",
                "dialogue_ids": [],
                "sound_cues": ["脚步声"],
            },
            {
                "beat_id": "beat-2",
                "camera": "中景平视",
                "action": "Mia打开饼干罐",
                "dialogue_ids": [],
                "sound_cues": ["罐盖轻响"],
            },
            {
                "beat_id": "beat-3",
                "camera": "近景轻微推进",
                "action": "Mia看见空罐并露出惊讶表情",
                "dialogue_ids": [],
                "sound_cues": ["轻微惊讶声"],
            },
        ]
        template = payload["shots"][0]
        payload["shots"] = []
        for index in range(8):
            shot = deepcopy(template)
            shot["shot_no"] = index + 1
            payload["shots"].append(shot)
        shots = parse_shot_breakdown_response(
            text=json.dumps(payload, ensure_ascii=False),
            characters=[self.rabbit],
            scenes=[self.scene],
            props=[self.jar],
            style_sentence=STYLE,
            segment_contract=contract,
        )

        self.assertEqual(shots[0]["duration_sec"], 11)
        self.assertEqual(len(shots), 8)
        self.assertEqual(
            shots[0]["shot_card"]["segment_plan"]["duration_mode"],
            "fixed",
        )
        self.assertEqual(shots[0]["shot_card"]["segment_plan"]["planned_duration_sec"], 11)
        self.assertEqual(shots[0]["shot_card"]["segment_plan"]["internal_shot_count"], 3)

    def test_parser_keeps_detailed_prompt_spaces_and_structured_entities(self):
        shots = parse_shot_breakdown_response(
            text=json.dumps(self._payload(), ensure_ascii=False),
            characters=[self.rabbit],
            scenes=[self.scene],
            props=[self.jar],
            style_sentence=STYLE,
        )

        self.assertEqual(set(shots[0]["image_prompts"]), {"shot_storyboard"})
        storyboard = shots[0]["image_prompts"]["shot_storyboard"]
        self.assertNotIn(
            "duration_rationale",
            shots[0]["shot_card"]["motion_timing"],
        )
        self.assertIn("Mia Rabbit", storyboard["positive_prompt"])
        self.assertIn("仍然盖着盖子的饼干罐", storyboard["positive_prompt"])
        self.assertNotIn(STYLE, storyboard["positive_prompt"])
        self.assertEqual(storyboard["visible_character_ids"], ["mia"])
        self.assertEqual(storyboard["visible_prop_ids"], ["jar"])
        self.assertNotIn("MiaRabbit", storyboard["positive_prompt"])
        self.assertNotIn("风格DNA", storyboard["positive_prompt"])

    def test_parser_ignores_unbound_reference_id(self):
        payload = self._payload()
        payload["shots"][0]["image_prompts"]["shot_storyboard"]["visible_prop_ids"] = ["unknown"]

        shots = parse_shot_breakdown_response(
            text=json.dumps(payload, ensure_ascii=False),
            characters=[self.rabbit],
            scenes=[self.scene],
            props=[self.jar],
            style_sentence=STYLE,
        )

        self.assertEqual(shots[0]["image_prompts"]["shot_storyboard"]["visible_prop_ids"], [])

    def test_parser_strips_internal_timestamps_but_keeps_ordered_actions(self):
        payload = self._payload()
        payload["shots"][0]["shot_card"]["beats"] = [
            {
                "beat_id": "beat-1",
                "camera": "0.0-2.0秒，全景建立空间",
                "action": "0.0-2.0秒，Mia先打开罐盖；2.0秒后低头查看空罐。",
                "dialogue_ids": [],
                "sound_cues": [],
            }
        ]

        shot = parse_shot_breakdown_response(
            text=json.dumps(payload, ensure_ascii=False),
            characters=[self.rabbit],
            scenes=[self.scene],
            props=[self.jar],
        )[0]

        beat = shot["shot_card"]["beats"][0]
        self.assertEqual(beat["camera"], "全景建立空间")
        self.assertEqual(beat["action"], "Mia先打开罐盖；低头查看空罐。")

    def test_segment_contract_rejects_total_duration_outside_project_budget(self):
        payload = self._payload()
        payload["shots"][0]["shot_card"]["beats"] = [
            {"beat_id": "beat-1", "camera": "全景", "action": "Mia靠近罐子", "dialogue_ids": [], "sound_cues": []},
            {"beat_id": "beat-2", "camera": "近景", "action": "Mia打开罐子", "dialogue_ids": [], "sound_cues": []},
        ]
        payload["shots"][0]["duration_sec"] = 4
        payload["shots"] = [
            {**deepcopy(payload["shots"][0]), "shot_no": index + 1}
            for index in range(8)
        ]

        with self.assertRaisesRegex(ValueError, "规划总时长 32 秒"):
            parse_shot_breakdown_response(
                text=json.dumps(payload, ensure_ascii=False),
                characters=[self.rabbit],
                scenes=[self.scene],
                props=[self.jar],
                segment_contract=segment_planning_contract(90),
            )

    def test_parser_fills_optional_shot_fields_without_extra_frame_prompts(self):
        payload = {
            "shots": [
                {
                    "description": "Mia Rabbit 发现饼干罐空了",
                    "scene_id": "unknown-scene",
                    "character_ids": ["mia", "unknown-character"],
                    "prop_ids": ["jar"],
                    "duration_sec": "not-a-number",
                    "shot_card": {
                        "image_prompts": {
                            "shot_storyboard": {
                                "positive_prompt": "Mia Rabbit 双手刚握住仍然盖着盖子的饼干罐"
                            }
                        }
                    },
                }
            ]
        }

        shots = parse_shot_breakdown_response(
            text=json.dumps(payload, ensure_ascii=False),
            characters=[self.rabbit],
            scenes=[self.scene],
            props=[self.jar],
            style_sentence=STYLE,
        )

        shot = shots[0]
        self.assertIsNone(shot["scene_id"])
        self.assertEqual(shot["character_ids"], ["mia"])
        self.assertIsNone(shot["duration_sec"])
        self.assertTrue(all("duration_sec" not in beat for beat in shot["shot_card"]["beats"]))
        self.assertEqual(set(shot["image_prompts"]), {"shot_storyboard"})

    def test_parser_does_not_truncate_long_prompt(self):
        payload = self._payload()
        long_detail = "木质厨房台面上散落细小面粉颗粒，" * 80
        payload["shots"][0]["image_prompts"]["shot_storyboard"]["positive_prompt"] += long_detail
        shots = parse_shot_breakdown_response(
            text=json.dumps(payload, ensure_ascii=False),
            characters=[self.rabbit],
            scenes=[self.scene],
            props=[self.jar],
            style_sentence=STYLE,
        )
        self.assertTrue(shots[0]["image_prompt"].endswith(long_detail))


if __name__ == "__main__":
    unittest.main()
