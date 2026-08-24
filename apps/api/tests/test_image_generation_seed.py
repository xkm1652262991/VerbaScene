import unittest

from app.core.config import settings
from app.services.image_generation_service import _image_candidate_count, _image_generation_params


class ImageGenerationSeedTests(unittest.TestCase):
    def setUp(self):
        self.original_image_seed = settings.image_seed
        settings.image_seed = 20260616

    def tearDown(self):
        settings.image_seed = self.original_image_seed

    def _params(self, *, task_id: str = "task-1", version: int = 1, extra_params=None):
        return _image_generation_params(
            task_id=task_id,
            target_kind="candidate",
            entity_type="character",
            entity_id="character-1",
            asset_role="character_main_ref",
            version=version,
            extra_params=extra_params,
        )

    def test_same_request_identity_is_reproducible(self):
        first = self._params()
        second = self._params()

        self.assertEqual(first, second)
        self.assertEqual(first[2], "task_scoped_v1")

    def test_new_regeneration_task_changes_seed(self):
        first_params, first_seed, _ = self._params(task_id="regenerate-click-1")
        second_params, second_seed, _ = self._params(task_id="regenerate-click-2")

        self.assertNotEqual(first_seed, second_seed)
        self.assertEqual(first_params["seed"], first_seed)
        self.assertEqual(second_params["seed"], second_seed)
        self.assertGreaterEqual(first_seed, 1)
        self.assertGreaterEqual(second_seed, 1)
        self.assertLessEqual(first_seed, (1 << 31) - 1)
        self.assertLessEqual(second_seed, (1 << 31) - 1)

    def test_candidate_version_changes_seed_within_task(self):
        _, first_seed, _ = self._params(version=1)
        _, second_seed, _ = self._params(version=2)

        self.assertNotEqual(first_seed, second_seed)

    def test_explicit_seed_is_preserved_for_targeted_ab(self):
        params, seed, strategy = self._params(extra_params={"seed": 42, "steps": 50})

        self.assertEqual(seed, 42)
        self.assertEqual(strategy, "explicit")
        self.assertEqual(params, {"seed": 42, "steps": 50})

    def test_one_click_always_creates_one_candidate_per_target(self):
        original_candidate_count = settings.image_candidate_count
        try:
            # Older saved configs may still contain 3. Workflow policy wins.
            settings.image_candidate_count = 3
            self.assertEqual(_image_candidate_count(), 1)
        finally:
            settings.image_candidate_count = original_candidate_count


if __name__ == "__main__":
    unittest.main()
