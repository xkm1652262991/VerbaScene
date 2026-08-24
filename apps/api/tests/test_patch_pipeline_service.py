import unittest

from app.services.patch_pipeline_service import AVD_PATCH_PIPELINE_VERSION, affected_stages_for_patch


class PatchPipelineServiceTests(unittest.TestCase):
    def test_stage_mapping_for_character_patch(self):
        self.assertEqual(
            affected_stages_for_patch(target_type="character"),
            ["assets", "production", "export"],
        )

    def test_stage_mapping_for_single_shot_patch(self):
        self.assertEqual(
            affected_stages_for_patch(target_type="shot"),
            ["production", "export"],
        )

    def test_stage_mapping_for_asset_patch(self):
        self.assertEqual(
            affected_stages_for_patch(target_type="asset", asset_type="image"),
            ["production", "export"],
        )
        self.assertEqual(
            affected_stages_for_patch(target_type="asset", asset_type="audio"),
            [],
        )

    def test_engine_version_is_stable(self):
        self.assertEqual(AVD_PATCH_PIPELINE_VERSION, "avd-patch-pipeline-v1")


if __name__ == "__main__":
    unittest.main()
