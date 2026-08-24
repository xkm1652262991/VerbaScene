import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from app.db.session import create_database_engine, initialize_database
from app.models import Project, Shot
from app.providers.types import ProviderAsset, ProviderResponse, ProviderStatus
from app.services.asset_resolver_service import AssetResolution
from app.services.asset_service import generate_single_shot_image_candidate
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


class SingleShotImageGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(
            f"sqlite+pysqlite:///{(Path(self.temp_dir.name) / 'single-shot-image.sqlite3').as_posix()}"
        )
        initialize_database(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_generates_one_pending_candidate_for_requested_shot(self):
        provider = _ImageProvider()
        selection = ImageProviderSelection(
            provider=provider,
            profile_id="dashscope-profile",
            default_params={"size": "1280x720", "reference_transport": "hybrid"},
            source="saved",
        )
        with self.session_factory() as db:
            project = Project(title="单片段生成", style="明亮儿童二维动画")
            db.add(project)
            db.flush()
            shot = Shot(
                project_id=project.id,
                shot_no=3,
                description="Mia在教室里举起红色小球",
                image_prompt="明亮教室，Mia举起红色小球，中景，儿童二维动画",
                negative_prompt="模糊，畸形，字幕",
                shot_card={"prompt_compiler": {"provider_profile": "dashscope_image"}},
                status="approved",
            )
            db.add(shot)
            db.commit()

            resolution = AssetResolution(
                stage="shot_image",
                project_id=project.id,
                shot_id=shot.id,
            )
            with (
                patch(
                    "app.services.image_generation_service.assert_project_pre_image_requirements"
                ) as assert_pre_image,
                patch(
                    "app.services.image_generation_service.resolve_image_provider_selection",
                    return_value=selection,
                ) as resolve_profile,
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
                candidate, task = generate_single_shot_image_candidate(
                    db,
                    shot.id,
                    image_provider_profile_id="dashscope-profile",
                )

            self.assertEqual(candidate.entity_id, shot.id)
            self.assertEqual(candidate.asset_role, "shot_storyboard")
            self.assertEqual(candidate.status, "pending_review")
            self.assertEqual(task.task_type, "single_shot_image_candidate_generation")
            self.assertEqual(task.input_payload["shot_id"], shot.id)
            self.assertEqual(len(provider.requests), 1)
            self.assertIn("视觉风格：明亮儿童二维动画", provider.requests[0].prompt)
            self.assertTrue(provider.requests[0].prompt.endswith(shot.image_prompt))
            self.assertEqual(provider.requests[0].negative_prompt, shot.negative_prompt)
            assert_pre_image.assert_called_once_with(db, project.id, shot_ids={shot.id})
            resolve_profile.assert_called_once_with(db, "dashscope-profile")


if __name__ == "__main__":
    unittest.main()
