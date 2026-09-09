from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from app.platform.media.store import LocalMediaStore
from app.agents.shot_breakdown import segment_planning_contract
from app.production.shot_direction.asset_inspector import MetadataAssetInspector
from app.production.shot_direction.contracts import (
    ShotDirectionInput,
    parse_and_apply_shot_patch,
    parse_reflection_response,
    parse_shot_draft_response,
    reflection_requires_patch,
)


class ShotDirectionContractTests(unittest.TestCase):
    def _source(self, *, target_duration_sec: int = 24, assets=None) -> ShotDirectionInput:
        return ShotDirectionInput(
            project_id="project-1",
            script_id="script-1",
            title="一起收拾",
            style="明亮儿童动画",
            target_duration_sec=target_duration_sec,
            aspect_ratio="16:9",
            resolution="854x480",
            creative_settings={"english_level": "A1"},
            script_content="Mia请Leo一起收拾。",
            script_scenes=[{"scene_no": 1, "visible_action": "两人一起收拾积木。"}],
            dialogues=[{"id": "dialogue-1", "speaker": "Mia", "text": "Can you help me?"}],
            characters=[
                {
                    "id": "character-1",
                    "name": "Mia",
                    "asset_spec": {
                        "state_variants": [{"key": "holding-block", "description": "拿着积木"}]
                    },
                }
            ],
            scenes=[{"id": "scene-1", "name": "活动室", "asset_spec": {}}],
            props=[{"id": "prop-1", "name": "积木", "asset_spec": {}}],
            adopted_assets=assets or [],
            segment_contract=segment_planning_contract(target_duration_sec),
        )

    def _shot(self, shot_no: int, *, dialogue_ids=None, description=None):
        dialogue_ids = list(dialogue_ids or [])
        return {
            "shot_no": shot_no,
            "description": description or f"片段 {shot_no} 推进收拾动作。",
            "camera_shot": "中景",
            "camera_movement": "固定机位",
            "duration_sec": 12,
            "scene_id": "scene-1",
            "character_ids": ["character-1"],
            "prop_ids": ["prop-1"],
            "dialogue_ids": dialogue_ids,
            "video_prompt": "",
            "generation_mode": "image_to_video",
            "shot_card": {
                "schema_version": 3,
                "source_scene_no": 1,
                "story_purpose": "推进合作",
                "emotional_intent": "友好",
                "duration_rationale": "完整承载发现积木和开始收拾的连续事件。",
                "camera": {"shot_size": "中景", "angle": "平视", "movement": "固定机位"},
                "action": {
                    "start_state": "积木散落。",
                    "main_action": "Mia拿起积木。",
                    "end_state": "积木进入盒子。",
                },
                "frame_plan": {
                    "first_frame": "Mia正要拿积木。",
                    "key_frame": "Mia拿起积木。",
                    "last_frame": "积木进入盒子。",
                },
                "storyboard_frame": {
                    "description": "Mia站在散落的积木旁。",
                    "narrative_focus": "合作开始",
                    "character_ids": ["character-1"],
                    "prop_ids": ["prop-1"],
                },
                "beats": [
                    {
                        "beat_id": "beat-1",
                        "camera": "中景固定机位",
                        "action": "Mia看见积木。",
                        "dialogue_ids": dialogue_ids,
                        "sound_cues": [],
                    },
                    {
                        "beat_id": "beat-2",
                        "camera": "近景轻微推进",
                        "action": "Mia拿起积木。",
                        "dialogue_ids": [],
                        "sound_cues": ["积木轻碰声"],
                    },
                ],
            },
            "image_prompts": {
                "shot_storyboard": {
                    "positive_prompt": "Mia站在活动室的散落积木旁，正准备弯腰。",
                    "negative_prompt": "字幕，水印，logo，可读文字",
                    "visible_character_ids": ["character-1"],
                    "visible_prop_ids": ["prop-1"],
                }
            },
        }

    def _draft(self, source):
        return parse_shot_draft_response(
            json.dumps(
                {
                    "shots": [
                        self._shot(1, dialogue_ids=["dialogue-1"]),
                        self._shot(2),
                    ]
                },
                ensure_ascii=False,
            ),
            source,
        )

    def test_editorial_note_does_not_authorize_patch(self):
        source = self._source()
        draft = self._draft(source)
        review = parse_reflection_response(
            json.dumps(
                {
                    "reflection_report": {
                        "summary": "结构成立",
                        "issues": [
                            {
                                "code": "optional_camera_flavor",
                                "category": "cinematography",
                                "severity": "editorial_note",
                                "message": "可选地增加一个更活泼的机位。",
                                "shot_nos": [1],
                            }
                        ],
                    }
                },
                ensure_ascii=False,
            ),
            draft=draft,
            source=source,
            asset_report={"missing_references": []},
            draft_contract={"errors": []},
        )

        self.assertFalse(reflection_requires_patch(review))
        self.assertEqual(review["issues"][0]["severity"], "editorial_note")

    def test_patch_changes_only_the_review_authorized_shot(self):
        source = self._source()
        draft = self._draft(source)
        replacement = self._shot(1, dialogue_ids=["dialogue-1"], description="片段 1 补足合作后的可见反应。")
        review = {
            "issues": [
                {
                    "code": "payoff_reaction_missing",
                    "category": "continuity",
                    "severity": "must_fix",
                    "message": "缺少动作结果后的反应。",
                    "shot_nos": [1],
                }
            ]
        }
        result = parse_and_apply_shot_patch(
            json.dumps(
                {
                    "shot_patch": {
                        "operations": [{"op": "replace", "shot_no": 1, "shot": replacement}],
                        "resolved_issue_codes": ["payoff_reaction_missing"],
                        "unresolved_issue_codes": [],
                    }
                },
                ensure_ascii=False,
            ),
            draft=draft,
            review=review,
            source=source,
        )

        self.assertEqual(result.patched_shot_nos, [1])
        self.assertIn("补足合作", result.shots[0]["description"])
        self.assertEqual(result.shots[1], draft[1])
        self.assertEqual(result.patch["resolved_issue_codes"], ["payoff_reaction_missing"])

    def test_patch_rejects_unreviewed_scope_and_unknown_entity_ids(self):
        source = self._source()
        draft = self._draft(source)
        review = {
            "issues": [
                {
                    "code": "shot_one_problem",
                    "category": "production",
                    "severity": "must_fix",
                    "message": "只允许修改片段 1。",
                    "shot_nos": [1],
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "not authorized"):
            parse_and_apply_shot_patch(
                json.dumps(
                    {
                        "shot_patch": {
                            "operations": [{"op": "remove", "shot_no": 2}],
                            "resolved_issue_codes": [],
                            "unresolved_issue_codes": ["shot_one_problem"],
                        }
                    }
                ),
                draft=draft,
                review=review,
                source=source,
            )

        invalid = deepcopy(self._shot(1, dialogue_ids=["dialogue-1"]))
        invalid["character_ids"] = ["hallucinated-character"]
        with self.assertRaisesRegex(ValueError, "unknown character_ids"):
            parse_and_apply_shot_patch(
                json.dumps(
                    {
                        "shot_patch": {
                            "operations": [{"op": "replace", "shot_no": 1, "shot": invalid}],
                            "resolved_issue_codes": [],
                            "unresolved_issue_codes": ["shot_one_problem"],
                        }
                    }
                ),
                draft=draft,
                review=review,
                source=source,
            )

    def test_metadata_inspector_reports_nonblocking_reference_gaps_without_requiring_props_or_states(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self._source(target_duration_sec=12)
            report = MetadataAssetInspector(LocalMediaStore(directory)).inspect(source)

        self.assertTrue(report["planning_ready"])
        self.assertFalse(report["generation_ready"])
        self.assertEqual(report["inspection_level"], "metadata_only")
        self.assertEqual(
            {(item["entity_type"], item["recommended_asset_role"]) for item in report["missing_references"]},
            {("character", "character_main_ref"), ("scene", "scene_ref")},
        )
        self.assertNotIn("prop", {item["entity_type"] for item in report["missing_references"]})
        self.assertNotIn("holding-block", json.dumps(report, ensure_ascii=False))
        self.assertTrue(all(item["blocking"] is False for item in report["missing_references"]))

    def test_metadata_inspector_finds_selected_version_and_local_uri_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            assets = [
                {
                    "id": "asset-1",
                    "asset_type": "image",
                    "asset_role": "character_main_ref",
                    "entity_type": "character",
                    "entity_id": "character-1",
                    "variant_key": "base",
                    "version": 1,
                    "uri": "/storage/projects/project-1/missing.png",
                    "status": "approved",
                    "is_selected": True,
                },
                {
                    "id": "asset-2",
                    "asset_type": "image",
                    "asset_role": "character_main_ref",
                    "entity_type": "character",
                    "entity_id": "character-1",
                    "variant_key": "base",
                    "version": 2,
                    "uri": "/storage/projects/project-1/also-missing.png",
                    "status": "approved",
                    "is_selected": True,
                },
                {
                    "id": "asset-3",
                    "asset_type": "image",
                    "asset_role": "scene_ref",
                    "entity_type": "scene",
                    "entity_id": "scene-1",
                    "variant_key": "base",
                    "version": 1,
                    "uri": "https://cdn.example.com/scene.png",
                    "status": "approved",
                    "is_selected": True,
                },
            ]
            source = self._source(target_duration_sec=12, assets=assets)
            report = MetadataAssetInspector(LocalMediaStore(Path(directory))).inspect(source)

        codes = {item["code"] for item in report["conflicts"]}
        self.assertIn("multiple_adopted_versions", codes)
        self.assertIn("local_media_unresolvable", codes)
        self.assertFalse(report["generation_ready"])
        self.assertEqual(report["missing_references"], [])


if __name__ == "__main__":
    unittest.main()
