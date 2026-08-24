from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.core.config import settings
from app.providers.defaults import provider_registry
from app.providers.types import ProviderResponse, ProviderStatus
from app.services.asset_resolver_service import AssetResolution, ResolvedReferenceAsset, resolve_generation_assets
from app.services.image_generation_service import _generate_image_asset


def _asset(asset_id: str, uri: str, role: str, entity_type: str, entity_id: str):
    return SimpleNamespace(
        id=asset_id,
        uri=uri,
        asset_type="image",
        asset_role=role,
        entity_type=entity_type,
        entity_id=entity_id,
        raw_response={},
    )


class AssetReferenceChainTests(unittest.TestCase):
    def setUp(self):
        self.shot = SimpleNamespace(
            id="shot-1",
            character_ids=["character-1"],
            scene_id="scene-1",
            prop_ids=["prop-1"],
            shot_card={},
        )
        self.assets = {
            ("character_main_ref", "character", "character-1"): _asset(
                "character-ref",
                "data:image/png;base64,Q0hBUkFDVEVS",
                "character_main_ref",
                "character",
                "character-1",
            ),
            ("scene_ref", "scene", "scene-1"): _asset(
                "scene-ref",
                "data:image/png;base64,U0NFTkU=",
                "scene_ref",
                "scene",
                "scene-1",
            ),
            ("prop_ref", "prop", "prop-1"): _asset(
                "prop-ref",
                "data:image/png;base64,UFJPUA==",
                "prop_ref",
                "prop",
                "prop-1",
            ),
            ("shot_storyboard", "shot", "shot-1"): _asset(
                "storyboard-ref",
                "data:image/png;base64,U1RPUllCT0FSRA==",
                "shot_storyboard",
                "shot",
                "shot-1",
            ),
            ("character_main_ref", "character", "character-2"): _asset(
                "character-ref-2",
                "data:image/png;base64,Q0hBUkFDVEVSMg==",
                "character_main_ref",
                "character",
                "character-2",
            ),
            ("character_main_ref", "character", "character-3"): _asset(
                "character-ref-3",
                "data:image/png;base64,Q0hBUkFDVEVSMw==",
                "character_main_ref",
                "character",
                "character-3",
            ),
        }

    def _selected_asset(self, _db, _project_id, _asset_type, role, entity_type, entity_id):
        return self.assets.get((role, entity_type, entity_id))

    def test_storyboard_generation_resolves_confirmed_entity_assets(self):
        with (
            patch(
                "app.services.asset_resolver_service._shot_prompt_context",
                return_value={"characters": [], "scene": None, "props": []},
            ),
            patch(
                "app.services.asset_resolver_service._selected_asset",
                side_effect=self._selected_asset,
            ),
            patch(
                "app.services.asset_resolver_service._selected_assets_by_role_prefix",
                return_value=[],
            ),
        ):
            resolution = resolve_generation_assets(
                SimpleNamespace(),
                "project-1",
                stage="shot_image",
                shot=self.shot,
            )

        self.assertEqual(
            [asset.reference_role for asset in resolution.reference_assets],
            ["character_main_ref", "scene_ref", "prop_ref"],
        )
        self.assertEqual(resolution.reference_metadata["reference_mode"], "entity_reference")
        self.assertEqual(len(resolution.references), 3)

    def test_video_generation_uses_first_frame_only_when_explicitly_supplied(self):
        with (
            patch(
                "app.services.asset_resolver_service._shot_prompt_context",
                return_value={"characters": [], "scene": None, "props": []},
            ),
            patch(
                "app.services.asset_resolver_service._selected_asset",
                side_effect=self._selected_asset,
            ),
            patch(
                "app.services.asset_resolver_service._selected_assets_by_role_prefix",
                return_value=[],
            ),
        ):
            resolution = resolve_generation_assets(
                SimpleNamespace(),
                "project-1",
                stage="shot_video",
                shot=self.shot,
                fallback_image_asset=self.assets[("shot_storyboard", "shot", "shot-1")],
            )

        self.assertEqual(
            [asset.reference_role for asset in resolution.reference_assets],
            ["video_first_frame", "character_main_ref", "scene_ref"],
        )
        self.assertNotIn(
            "prop-ref",
            [asset.asset_id for asset in resolution.reference_assets],
        )
        self.assertEqual(
            resolution.reference_metadata["reference_mode"],
            "manual_video_first_frame",
        )
        self.assertEqual(
            resolution.reference_metadata["storyboard_first_frame_asset_id"],
            "storyboard-ref",
        )
        self.assertEqual(
            resolution.reference_metadata["video_reference_policy"],
            "optional_first_frame_plus_2_characters_1_scene_v2",
        )

    def test_video_generation_does_not_auto_use_existing_storyboard(self):
        with (
            patch(
                "app.services.asset_resolver_service._shot_prompt_context",
                return_value={"characters": [], "scene": None, "props": []},
            ),
            patch(
                "app.services.asset_resolver_service._selected_asset",
                side_effect=self._selected_asset,
            ),
            patch(
                "app.services.asset_resolver_service._selected_assets_by_role_prefix",
                return_value=[],
            ),
        ):
            resolution = resolve_generation_assets(
                SimpleNamespace(),
                "project-1",
                stage="shot_video",
                shot=self.shot,
            )

        self.assertEqual(
            [asset.reference_role for asset in resolution.reference_assets],
            ["character_main_ref", "scene_ref"],
        )
        self.assertEqual(
            resolution.reference_metadata["reference_mode"],
            "entity_references",
        )
        self.assertIsNone(
            resolution.reference_metadata["video_first_frame_asset_id"],
        )

    def test_video_generation_caps_character_images_at_two(self):
        shot = SimpleNamespace(
            id="shot-1",
            character_ids=["character-1", "character-2", "character-3"],
            scene_id="scene-1",
            prop_ids=["prop-1"],
            shot_card={},
        )
        with (
            patch(
                "app.services.asset_resolver_service._shot_prompt_context",
                return_value={"characters": [], "scene": None, "props": []},
            ),
            patch(
                "app.services.asset_resolver_service._selected_asset",
                side_effect=self._selected_asset,
            ),
        ):
            resolution = resolve_generation_assets(
                SimpleNamespace(),
                "project-1",
                stage="shot_video",
                shot=shot,
                fallback_image_asset=self.assets[("shot_storyboard", "shot", "shot-1")],
            )

        self.assertEqual(
            [asset.asset_id for asset in resolution.reference_assets],
            ["storyboard-ref", "character-ref", "character-ref-2", "scene-ref"],
        )

    def test_image_generation_service_forwards_resolved_references_to_provider(self):
        resolution = AssetResolution(
            stage="shot_image",
            project_id="project-1",
            shot_id="shot-1",
            reference_assets=[
                ResolvedReferenceAsset(
                    asset_id="character-ref",
                    uri="data:image/png;base64,Q0hBUkFDVEVS",
                    asset_type="image",
                    asset_role="character_main_ref",
                    entity_type="character",
                    entity_id="character-1",
                    reference_role="character_main_ref",
                    priority=30,
                ),
                ResolvedReferenceAsset(
                    asset_id="scene-ref",
                    uri="data:image/png;base64,U0NFTkU=",
                    asset_type="image",
                    asset_role="scene_ref",
                    entity_type="scene",
                    entity_id="scene-1",
                    reference_role="scene_ref",
                    priority=31,
                ),
            ],
            reference_metadata={"reference_mode": "entity_reference"},
        )
        provider = SimpleNamespace(
            name="reference-provider",
            model="reference-model",
            capabilities=["text_to_image", "reference_payload"],
            submit=Mock(
                return_value=ProviderResponse(
                    status=ProviderStatus.SUCCEEDED,
                    provider_task_id="provider-task",
                )
            ),
        )
        original_transport = settings.image_reference_transport
        try:
            settings.image_reference_transport = "hybrid"
            with (
                patch.object(provider_registry, "get", return_value=provider),
                patch("app.services.image_generation_service.next_asset_version", return_value=1),
                patch("app.services.image_generation_service._persist_provider_image_asset", return_value=SimpleNamespace(id="asset-1")),
            ):
                _generate_image_asset(
                    SimpleNamespace(),
                    project_id="project-1",
                    task_id="task-1",
                    entity_type="shot",
                    entity_id="shot-1",
                    asset_role="shot_storyboard",
                    prompt="分镜图",
                    negative_prompt=None,
                    asset_resolution=resolution,
                )
        finally:
            settings.image_reference_transport = original_transport

        request = provider.submit.call_args.args[0]
        self.assertEqual(request.references, resolution.references)
        self.assertEqual(request.params["asset_reference_transport"], "hybrid")
        self.assertEqual(request.params["asset_reference_metadata"]["reference_mode"], "entity_reference")
        self.assertEqual(request.metadata["asset_resolution"]["reference_assets"][0]["asset_id"], "character-ref")

if __name__ == "__main__":
    unittest.main()
