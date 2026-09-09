import json
import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from app.agents.entity_autofill import autofill_entity_asset_specs
from app.agents.entity_design import (
    active_asset_state_text,
    compile_character_asset_fields,
    compile_prop_asset_fields,
    compile_scene_asset_fields,
    normalize_character_asset_spec,
    normalize_prop_asset_spec,
    normalize_scene_asset_spec,
)
from app.agents.entity_extraction import (
    build_entity_extraction_prompt,
    parse_entity_extraction_response,
)
from app.agents.shot_breakdown import build_shot_breakdown_prompt
from app.schemas.entity import CharacterUpdate, PropUpdate, SceneUpdate


class EntityDesignTests(unittest.TestCase):
    def test_sparse_extraction_is_autofilled_from_script_and_type_templates(self):
        sparse = parse_entity_extraction_response(
            json.dumps(
                {
                    "characters": [
                        {
                            "name": "小鸟",
                            "role_type": "配角",
                            "identity": "Tom 救助的小鸟",
                            "asset_spec": {},
                        }
                    ],
                    "scenes": [{"name": "树下", "asset_spec": {}}],
                    "props": [{"name": "纸盒", "asset_spec": {}}],
                },
                ensure_ascii=False,
            )
        )
        script_scenes = [
            {
                "scene_no": 1,
                "title": "树下发现",
                "location": "梧桐树下",
                "time_of_day": "下午",
                "characters": ["Tom", "小鸟"],
                "props": ["纸盒"],
                "visible_action": "Tom 发现右翼受伤下垂的小鸟，并把它放进纸盒。",
                "story_purpose": "建立 Tom 救助小鸟的行动。",
                "source_evidence": "Tom 在树下发现受伤的小鸟。",
                "dialogues": [],
            },
            {
                "scene_no": 3,
                "title": "窗外返回",
                "location": "窗外枝头",
                "time_of_day": "清晨",
                "characters": ["小鸟"],
                "props": [],
                "visible_action": "已康复的小鸟展开双翼，飞回窗外枝头鸣叫。",
                "story_purpose": "表现小鸟康复并返回。",
                "source_evidence": "小鸟一周后康复飞走，第二天返回。",
                "dialogues": [],
            },
        ]

        result = autofill_entity_asset_specs(
            sparse,
            script_scenes=script_scenes,
            script_content="Tom 在树下发现受伤的小鸟，并把它放进纸盒。小鸟康复后飞走，第二天返回。",
        )

        bird = result["characters"][0]
        tree = result["scenes"][0]
        box = result["props"][0]
        self.assertEqual(bird["asset_spec"]["entity_kind"], "animal")
        self.assertEqual(bird["asset_spec"]["species"], "小型鸟类")
        self.assertTrue(bird["asset_spec"]["body_type"])
        self.assertTrue(bird["asset_spec"]["color_palette"])
        self.assertEqual(
            {state["name"] for state in bird["asset_spec"]["state_variants"]},
            {"受伤", "已康复"},
        )
        self.assertNotIn("受伤", bird["fixed_prompt"] or "")
        self.assertEqual(bird["asset_spec"]["reference_plan"]["views"][2], "展翼姿态参考")
        self.assertTrue(tree["asset_spec"]["spatial_layout"])
        self.assertIn("树皮", tree["asset_spec"]["materials"])
        self.assertEqual(box["asset_spec"]["shape"], "浅口长方体纸盒")
        self.assertTrue(box["asset_spec"]["source_evidence"])
        self.assertTrue(bird["asset_spec"]["autofill"]["applied"])
        self.assertIn("body_type", bird["asset_spec"]["autofill"]["review_fields"])

    def test_autofill_preserves_model_values_and_does_not_fabricate_evidence(self):
        sparse = parse_entity_extraction_response(
            json.dumps(
                {
                    "characters": [
                        {
                            "name": "凭空人物",
                            "role_type": "配角",
                            "age": "成年",
                            "gender": "男",
                            "asset_spec": {
                                "body_type": "模型已确定的修长体型",
                                "color_palette": ["红色", "黑色"],
                                "reference_plan": {
                                    "required": False,
                                    "priority": "optional",
                                    "views": [],
                                },
                            },
                        }
                    ],
                    "scenes": [{"name": "凭空地点", "asset_spec": {}}],
                    "props": [],
                },
                ensure_ascii=False,
            )
        )

        result = autofill_entity_asset_specs(
            sparse,
            script_scenes=[
                {
                    "scene_no": 1,
                    "title": "真实场次",
                    "location": "树下",
                    "characters": ["Tom"],
                    "props": [],
                    "visible_action": "Tom 在树下停下。",
                }
            ],
            script_content="Tom 在树下停下。",
        )
        character = result["characters"][0]

        self.assertEqual(character["asset_spec"]["body_type"], "模型已确定的修长体型")
        self.assertEqual(character["asset_spec"]["color_palette"], ["红色", "黑色"])
        self.assertEqual(
            character["asset_spec"]["reference_plan"],
            {"required": False, "priority": "optional", "views": []},
        )
        self.assertEqual(character["asset_spec"]["source_evidence"], [])

    def test_autofill_moves_mutable_character_clause_out_of_stable_identity(self):
        parsed = parse_entity_extraction_response(
            json.dumps(
                {
                    "characters": [
                        {
                            "name": "小鸟",
                            "role_type": "配角",
                            "identity": "野生雀形目鸟类",
                            "asset_spec": {
                                "entity_kind": "animal",
                                "species": "雀形目小型鸟",
                                "body_type": "纤细鸟体，翼展与身长比稳定",
                                "facial_features": "黑亮圆眼，短喙微弯",
                                "hair_or_surface": "灰褐色蓬松羽毛，尾羽略分叉",
                                "color_palette": ["灰褐", "浅棕", "奶油白"],
                                "signature_features": ["右翼自然下垂，羽尖微乱；眼周绒毛略软"],
                                "state_variants": [
                                    {
                                        "name": "右翼受伤",
                                        "description": "右翼无法展开，呈松弛下垂状",
                                        "scene_nos": [1],
                                    }
                                ],
                                "source_evidence": [
                                    {"scene_no": 1, "evidence": "Tom在树下发现一只右翼受伤的小鸟。"}
                                ],
                                "reference_plan": {
                                    "required": True,
                                    "priority": "core",
                                    "views": ["侧面全身"],
                                },
                            },
                        }
                    ],
                    "scenes": [{"name": "树下", "asset_spec": {}}],
                    "props": [],
                },
                ensure_ascii=False,
            )
        )
        result = autofill_entity_asset_specs(
            parsed,
            script_scenes=[
                {
                    "scene_no": 1,
                    "title": "树下相遇",
                    "characters": [],
                    "props": [],
                    "visible_action": "Tom在树下发现一只右翼受伤的小鸟，轻柔拾起。",
                    "source_evidence": "Tom在树下发现一只右翼受伤的小鸟。",
                }
            ],
            script_content="Tom在树下发现一只右翼受伤的小鸟。",
        )

        bird = result["characters"][0]
        self.assertEqual(bird["asset_spec"]["signature_features"], ["眼周绒毛略软"])
        self.assertNotIn("右翼自然下垂", bird["fixed_prompt"] or "")
        self.assertIn("signature_features", bird["asset_spec"]["autofill"]["normalized_fields"])

    def test_prop_holder_relation_is_not_compiled_into_stable_visual_prompt(self):
        spec = normalize_prop_asset_spec(
            {
                "shape": "细长透明管身",
                "dimensions": "与角色食指等长",
                "materials": ["透明塑料"],
                "color_palette": ["透明"],
                "signature_features": ["刻度线位置固定"],
                "scale_reference": "长度约为手掌的三分之二",
                "holder_relation": "由Tom右手持握，尖端朝下",
                "story_function": "喂水工具",
            }
        )
        description, visual_prompt, _ = compile_prop_asset_fields(name="小滴管", asset_spec=spec)

        self.assertNotIn("手持", description or "")
        self.assertNotIn("持握", visual_prompt or "")
        self.assertEqual(spec["holder_relation"], "由Tom右手持握，尖端朝下")

    def test_temporary_character_state_is_not_compiled_into_stable_identity(self):
        spec = normalize_character_asset_spec(
            {
                **self._bird_spec(),
                "state_variants": [
                    {
                        "name": "右翼受伤",
                        "description": "右翼下垂，羽毛微乱，不能起飞",
                        "scene_nos": [1],
                    },
                    {
                        "name": "已康复",
                        "description": "双翼展开对称，可正常飞行",
                        "scene_nos": [3],
                    },
                ],
            }
        )

        appearance, fixed_prompt = compile_character_asset_fields(
            name="小鸟",
            identity="Tom 救助的野生麻雀",
            age=None,
            gender=None,
            asset_spec=spec,
        )

        self.assertNotIn("受伤", appearance or "")
        self.assertNotIn("右翼下垂", fixed_prompt or "")
        self.assertIn("右翼下垂", active_asset_state_text(spec, 1))
        self.assertEqual(active_asset_state_text(spec, 2), "")
        self.assertIn("已康复", active_asset_state_text(spec, 3))

    def test_scene_variants_do_not_pollute_legacy_atmosphere_or_fixed_prompt(self):
        spec = normalize_scene_asset_spec(
            {
                "location_type": "室内卧室",
                "spatial_layout": "窗台在北墙，书桌紧贴窗台下方",
                "fixed_landmarks": ["北墙窗台", "木质书桌"],
                "materials": ["白墙", "浅木色桌面"],
                "color_palette": ["米白", "浅木色"],
                "zones": ["窗台", "书桌"],
                "state_variants": [
                    {"name": "清晨", "description": "柔和日光从窗外照入", "scene_nos": [2]}
                ],
                "source_evidence": [{"scene_no": 2, "evidence": "Tom 在卧室窗台旁照料小鸟。"}],
                "reference_plan": {"required": True, "priority": "supporting", "views": ["空间全景"]},
            }
        )

        _, _, atmosphere, fixed_prompt = compile_scene_asset_fields(name="Tom 的卧室", asset_spec=spec)

        self.assertIsNone(atmosphere)
        self.assertNotIn("清晨", fixed_prompt or "")
        self.assertIn("柔和日光", active_asset_state_text(spec, 2))

    def test_structured_entity_response_compiles_and_passes_quality_gate(self):
        payload = {
            "characters": [
                {
                    "name": "小鸟",
                    "role_type": "配角",
                    "age": None,
                    "gender": None,
                    "identity": "Tom 救助的野生麻雀",
                    "personality": "警觉，逐渐信任 Tom",
                    "asset_spec": {
                        **self._bird_spec(),
                        "state_variants": [
                            {"name": "受伤", "description": "右翼下垂，不能起飞", "scene_nos": [1]}
                        ],
                    },
                }
            ],
            "scenes": [
                {
                    "name": "树下",
                    "asset_spec": {
                        "aliases": [],
                        "location_type": "室外树下",
                        "spatial_layout": "梧桐树居中，树下是平坦草地",
                        "fixed_landmarks": ["梧桐树", "草地"],
                        "materials": ["树皮", "草叶"],
                        "color_palette": ["树皮棕", "草叶绿"],
                        "zones": ["树干旁", "草地"],
                        "state_variants": [],
                        "source_evidence": [{"scene_no": 1, "evidence": "Tom 在树下发现小鸟。"}],
                        "reference_plan": {"required": True, "priority": "supporting", "views": ["空间全景"]},
                    },
                }
            ],
            "props": [
                {
                    "name": "纸盒",
                    "asset_spec": {
                        "aliases": [],
                        "shape": "浅口长方体纸盒",
                        "dimensions": "儿童可用双手托起",
                        "materials": ["瓦楞纸"],
                        "color_palette": ["浅棕色"],
                        "signature_features": ["可翻折盒盖", "清晰纸面纹理"],
                        "scale_reference": "宽度约为 Tom 肩宽的一半",
                        "holder_relation": "Tom 可双手托持",
                        "story_function": "临时安置小鸟",
                        "state_variants": [],
                        "source_evidence": [{"scene_no": 1, "evidence": "Tom 把小鸟放进纸盒。"}],
                        "reference_plan": {"required": True, "priority": "supporting", "views": ["三分之四视角"]},
                    },
                }
            ],
        }

        result = parse_entity_extraction_response(json.dumps(payload, ensure_ascii=False))

        self.assertNotIn("受伤", result["characters"][0]["fixed_prompt"] or "")
        self.assertEqual(result["characters"][0]["asset_spec"]["state_variants"][0]["scene_nos"], [1])

    def test_prompts_expose_structured_contract_and_source_scene_number(self):
        project = SimpleNamespace(title="英语教学2", style="二维手绘动画，线条稳定")
        script = SimpleNamespace(
            content="Tom 在树下发现小鸟。",
            scenes=[{"scene_no": 1, "title": "树下", "visible_action": "Tom 发现小鸟"}],
        )
        character = SimpleNamespace(
            id="character-1",
            name="Tom",
            role_type="主角",
            identity="学生",
            appearance="短黑发",
            fixed_prompt="Tom，短黑发",
            asset_spec={"state_variants": []},
        )
        scene = SimpleNamespace(
            id="scene-1",
            name="树下",
            description="梧桐树下",
            visual_style="树皮与草叶",
            atmosphere=None,
            fixed_prompt="梧桐树下",
            asset_spec={"state_variants": []},
        )

        entity_prompt = build_entity_extraction_prompt(project, script)
        shot_prompt = build_shot_breakdown_prompt(
            script=script,
            characters=[character],
            scenes=[scene],
            props=[],
            style=project.style,
        )

        self.assertIn("asset_spec", entity_prompt)
        self.assertIn('"scene_no": 1', entity_prompt)
        self.assertIn("所有资产描述、状态、证据概括和参考图 Prompt 必须使用自然、完整的简体中文", entity_prompt)
        self.assertIn("人物等专有名称的 name 可以保留原文", entity_prompt)
        self.assertIn("source_scene_no", shot_prompt)
        self.assertIn('"asset_spec"', shot_prompt)
        self.assertIn("所有面向用户和图片/视频模型的内容必须使用自然、完整的简体中文", shot_prompt)
        self.assertIn("人物专名如 Mia Rabbit 可以保留原文", shot_prompt)

    def test_manual_update_schema_rejects_compiled_prompt_fields(self):
        with self.assertRaises(ValidationError):
            CharacterUpdate.model_validate({"fixed_prompt": "手工覆盖"})

    def test_manual_update_schema_preserves_editable_image_prompts(self):
        cases = (
            (CharacterUpdate, "character_main_ref"),
            (SceneUpdate, "scene_ref"),
            (PropUpdate, "prop_ref"),
        )
        for schema, role in cases:
            with self.subTest(schema=schema.__name__):
                payload = schema.model_validate(
                    {
                        "asset_spec": {
                            "image_prompts": {
                                role: {
                                    "positive_prompt": "只保留干净主体",
                                    "negative_prompt": "多余角色",
                                }
                            }
                        }
                    }
                ).model_dump(exclude_none=True)

                self.assertEqual(
                    payload["asset_spec"]["image_prompts"][role],
                    {
                        "positive_prompt": "只保留干净主体",
                        "negative_prompt": "多余角色",
                    },
                )

    @staticmethod
    def _bird_spec():
        return {
            "aliases": ["麻雀"],
            "entity_kind": "animal",
            "species": "野生麻雀",
            "body_type": "小型鸟类，躯干圆润，翼展比例自然",
            "facial_features": "小型黑色眼睛，短尖喙",
            "hair_or_surface": "灰褐色短羽，背部略深",
            "color_palette": ["灰褐色", "浅米色", "黑色"],
            "default_outfit": "",
            "signature_features": ["眼周浅色羽圈", "短尖喙"],
            "default_accessories": [],
            "state_variants": [],
            "source_evidence": [{"scene_no": 1, "evidence": "Tom 在树下发现一只小鸟。"}],
            "reference_plan": {"required": True, "priority": "core", "views": ["侧面全身", "正面近景"]},
        }


if __name__ == "__main__":
    unittest.main()
