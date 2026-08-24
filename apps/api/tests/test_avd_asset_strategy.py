import unittest

from app.agents.avd_asset_strategy import (
    AVD_ASSET_STRATEGY_VERSION,
    avd_asset_metadata,
    build_avd_reference_strategy,
    infer_avd_asset_purpose,
)


class AVDAssetStrategyTests(unittest.TestCase):
    def test_infers_video_and_display_asset_purposes(self):
        self.assertEqual(
            infer_avd_asset_purpose(asset_type="image", asset_role="character_main_ref", entity_type="character"),
            "video_asset",
        )
        self.assertEqual(
            infer_avd_asset_purpose(asset_type="image", asset_role="shot_storyboard_grid", entity_type="grid"),
            "display_asset",
        )
        self.assertTrue(
            avd_asset_metadata(asset_type="image", asset_role="scene_ref", entity_type="scene")[
                "can_use_as_video_reference"
            ]
        )

    def test_keeps_explicit_asset_purpose_from_raw_response(self):
        raw_response = {
            "request_metadata": {
                "avd_asset_usage": {
                    "asset_purpose": "display_asset",
                }
            }
        }
        self.assertEqual(
            infer_avd_asset_purpose(
                asset_type="image",
                asset_role="shot_storyboard",
                entity_type="shot",
                raw_response=raw_response,
            ),
            "display_asset",
        )

    def test_shot_video_does_not_require_first_frame(self):
        strategy = build_avd_reference_strategy(
            stage="shot_video",
            character_ids=["char-1"],
            scene_id="scene-1",
            reference_assets=[
                {
                    "asset_id": "char-ref",
                    "asset_type": "image",
                    "asset_role": "character_main_ref",
                    "entity_type": "character",
                    "entity_id": "char-1",
                    "reference_role": "character_main_ref",
                }
            ],
        )

        self.assertEqual(strategy["version"], AVD_ASSET_STRATEGY_VERSION)
        self.assertEqual(strategy["blockers"], [])
        self.assertIn("缺少场景一致性参考图: scene:scene-1", strategy["warnings"])

    def test_shot_video_rejects_display_asset_reference(self):
        strategy = build_avd_reference_strategy(
            stage="shot_video",
            reference_assets=[
                {
                    "asset_id": "grid-ref",
                    "asset_type": "image",
                    "asset_role": "shot_storyboard_grid",
                    "entity_type": "grid",
                    "entity_id": "grid-1",
                    "reference_role": "video_first_frame",
                }
            ],
        )

        self.assertTrue(any("展示/营销用途资产" in blocker for blocker in strategy["blockers"]))

    def test_shot_video_honors_explicit_display_reference_purpose(self):
        strategy = build_avd_reference_strategy(
            stage="shot_video",
            reference_assets=[
                {
                    "asset_id": "manual-ref",
                    "asset_type": "image",
                    "asset_role": "shot_storyboard",
                    "entity_type": "shot",
                    "entity_id": "shot-1",
                    "reference_role": "video_first_frame",
                    "avd_asset_purpose": "display_asset",
                }
            ],
        )

        self.assertTrue(any("展示/营销用途资产" in blocker for blocker in strategy["blockers"]))

    def test_shot_image_satisfies_character_and_scene_requirements(self):
        strategy = build_avd_reference_strategy(
            stage="shot_image",
            character_ids=["char-1"],
            scene_id="scene-1",
            prop_ids=["prop-1"],
            reference_assets=[
                {
                    "asset_id": "char-ref",
                    "asset_type": "image",
                    "asset_role": "character_main_ref",
                    "entity_type": "character",
                    "entity_id": "char-1",
                    "reference_role": "character_main_ref",
                },
                {
                    "asset_id": "scene-ref",
                    "asset_type": "image",
                    "asset_role": "scene_ref",
                    "entity_type": "scene",
                    "entity_id": "scene-1",
                    "reference_role": "scene_ref",
                },
            ],
        )

        self.assertEqual(strategy["blockers"], [])
        self.assertIn("缺少道具参考图: prop:prop-1", strategy["warnings"])


if __name__ == "__main__":
    unittest.main()
