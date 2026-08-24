import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from app.db.session import create_database_engine, initialize_database
from fastapi import HTTPException

from app.models import Character, Project
from app.providers.types import ProviderAsset, ProviderResponse, ProviderStatus
from app.services.asset_resolver_service import AssetResolution
from app.services.asset_service import generate_single_reference_image_candidate
from app.services.image_provider_profile_service import ImageProviderSelection


class _ImageProvider:
    name = "dashscope_image"
    model = "qwen-image-2.0-pro-2026-06-22"
    capabilities = ["text_to_image", "reference_to_image", "reference_payload"]

    def __init__(self):
        self.requests = []

    def submit(self, request):
        self.requests.append(request)
        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id="provider-task-ok",
            assets=[
                ProviderAsset(
                    asset_type="image",
                    uri="data:image/png;base64,dGVzdA==",
                    mime_type="image/png",
                    width=1280,
                    height=720,
                )
            ],
        )


class SingleReferenceImageGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(
            f"sqlite+pysqlite:///{(Path(self.temp_dir.name) / 'single-reference-image.sqlite3').as_posix()}"
        )
        initialize_database(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_generates_character_base_multi_view_reference(self):
        provider = _ImageProvider()
        selection = ImageProviderSelection(
            provider=provider,
            profile_id="dashscope-profile",
            default_params={"size": "1280x720"},
            source="saved",
        )
        with self.session_factory() as db:
            project = Project(title="单张资产图", style="二维手绘动画")
            db.add(project)
            db.flush()
            character = Character(
                project_id=project.id,
                name="Mia Rabbit",
                fixed_prompt="角色主参考 Prompt",
                asset_spec={
                    "state_variants": [
                        {
                            "key": "holding_ball",
                            "name": "拿球",
                            "description": "Mia Rabbit双手抱着一个红色小球。",
                        }
                    ],
                    "image_prompts": {
                        "character_main_ref": {
                            "positive_prompt": "Mia Rabbit 的中文多视角角色设定图 Prompt",
                            "negative_prompt": "字幕，水印，logo，可读文字",
                        }
                    }
                },
                status="approved",
            )
            db.add(character)
            db.commit()

            resolution = AssetResolution(stage="reference_image", project_id=project.id)
            with (
                patch(
                    "app.services.image_generation_service.resolve_image_provider_selection",
                    return_value=selection,
                ),
                patch(
                    "app.services.image_generation_service.resolve_generation_assets",
                    return_value=resolution,
                ),
                patch(
                    "app.services.image_generation_service.materialize_data_uri",
                    side_effect=lambda _project_id, _candidate_id, uri, _mime_type: uri,
                ),
                patch("app.services.image_generation_service._image_consistency_check", return_value=None),
            ):
                candidate, task = generate_single_reference_image_candidate(
                    db,
                    project.id,
                    entity_type="character",
                    entity_id=character.id,
                    asset_role="character_main_ref",
                    variant_key="base",
                    image_provider_profile_id="dashscope-profile",
                )

            self.assertEqual(candidate.entity_id, character.id)
            self.assertEqual(candidate.asset_role, "character_main_ref")
            self.assertEqual(candidate.variant_key, "base")
            self.assertEqual(candidate.status, "pending_review")
            self.assertEqual(task.task_type, "single_reference_image_candidate_generation")
            self.assertEqual(len(provider.requests), 1)
            self.assertIn("视觉风格：二维手绘动画", provider.requests[0].prompt)
            self.assertTrue(
                provider.requests[0].prompt.endswith("Mia Rabbit 的中文多视角角色设定图 Prompt")
            )
            self.assertEqual(provider.requests[0].negative_prompt, "字幕，水印，logo，可读文字")

    def test_rejects_independent_character_state_variant_image(self):
        with self.session_factory() as db:
            project = Project(title="角色状态图", style="二维手绘动画")
            db.add(project)
            db.flush()
            character = Character(
                project_id=project.id,
                name="Mia Rabbit",
                fixed_prompt="角色主参考 Prompt",
                asset_spec={
                    "state_variants": [{
                        "key": "holding_ball",
                        "name": "拿球",
                        "description": "Mia Rabbit双手抱着一个红色小球。",
                    }],
                },
                status="approved",
            )
            db.add(character)
            db.commit()

            with self.assertRaises(HTTPException) as raised:
                generate_single_reference_image_candidate(
                    db,
                    project.id,
                    entity_type="character",
                    entity_id=character.id,
                    asset_role="character_main_ref",
                    variant_key="holding_ball",
                )

            self.assertEqual(raised.exception.status_code, 422)
            self.assertIn("prompt-only", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
