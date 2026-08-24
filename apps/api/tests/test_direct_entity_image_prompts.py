import json
import unittest

from app.agents.entity_autofill import autofill_entity_asset_specs
from app.agents.entity_extraction import parse_entity_extraction_response
from app.agents.style import strip_project_style


STYLE = "统一二维童话动画，干净线条，柔和赛璐璐上色"


class DirectEntityImagePromptTests(unittest.TestCase):
    def test_reference_prompts_survive_autofill_as_style_neutral_subjects(self):
        character_prompt = (
            "Mia Rabbit，soft white fur，light gray ear tips；同一张横向设定图依次展示"
            f"正面全身、四分之三全身、侧面全身和脸部近景，暖白背景；{STYLE}。"
        )
        payload = {
            "characters": [
                {
                    "name": "Mia Rabbit",
                    "role_type": "主角",
                    "identity": "一只小兔子",
                    "asset_spec": {"entity_kind": "animal", "species": "兔子"},
                    "reference_prompts": {
                        "character_main_ref": {"positive_prompt": character_prompt, "negative_prompt": ""},
                    },
                }
            ],
            "scenes": [
                {
                    "name": "厨房",
                    "asset_spec": {
                        "spatial_layout": "长方形房间，一侧台面，中央活动区",
                        "reference_prompt": {
                            "positive_prompt": f"长方形厨房全景，一侧台面和中央活动区清楚可见；{STYLE}。",
                            "negative_prompt": "",
                        },
                    },
                }
            ],
            "props": [
                {
                    "name": "饼干罐",
                    "asset_spec": {
                        "shape": "圆柱形带盖陶瓷罐",
                        "reference_prompt": {
                            "positive_prompt": f"单个圆柱形带盖陶瓷饼干罐，奶油白釉面；{STYLE}。",
                            "negative_prompt": "",
                        },
                    },
                }
            ],
        }

        parsed = parse_entity_extraction_response(
            json.dumps(payload, ensure_ascii=False),
            style_sentence=STYLE,
        )
        result = autofill_entity_asset_specs(
            parsed,
            script_scenes=[],
            script_content="Mia Rabbit 在厨房发现饼干罐空了。",
        )

        rabbit = result["characters"][0]
        neutral_character_prompt = strip_project_style(character_prompt, STYLE)
        self.assertEqual(
            rabbit["asset_spec"]["image_prompts"]["character_main_ref"]["positive_prompt"],
            neutral_character_prompt,
        )
        self.assertEqual(rabbit["fixed_prompt"], neutral_character_prompt)
        self.assertIn("Mia Rabbit", rabbit["fixed_prompt"])
        self.assertNotIn("MiaRabbit", rabbit["fixed_prompt"])
        self.assertEqual(
            rabbit["asset_spec"]["image_prompts"]["character_main_ref"]["negative_prompt"],
            "字幕，水印，logo，可读文字",
        )
        self.assertNotIn(STYLE, result["scenes"][0]["fixed_prompt"])
        self.assertNotIn(STYLE, result["props"][0]["visual_prompt"])

    def test_missing_or_non_exact_reference_prompt_uses_existing_description(self):
        payload = {
            "characters": [
                {
                    "name": "Mia Rabbit",
                    "asset_spec": {
                        "entity_kind": "animal",
                        "species": "兔子",
                        "body_type": "小巧直立体型",
                    },
                }
            ],
            "scenes": [
                {
                    "name": "厨房",
                    "asset_spec": {
                        "spatial_layout": "长方形厨房，一侧台面，中央活动区",
                        "reference_prompt": {
                            "positive_prompt": "长方形厨房全景，一侧台面和中央活动区清楚可见"
                        },
                    },
                }
            ],
            "props": [],
        }

        parsed = parse_entity_extraction_response(
            json.dumps(payload, ensure_ascii=False),
            style_sentence=STYLE,
        )

        self.assertNotIn("image_prompts", parsed["characters"][0]["asset_spec"])
        self.assertIn("Mia Rabbit", parsed["characters"][0]["fixed_prompt"])
        self.assertNotIn(STYLE, parsed["characters"][0]["fixed_prompt"])
        self.assertNotIn(STYLE, parsed["scenes"][0]["fixed_prompt"])


if __name__ == "__main__":
    unittest.main()
