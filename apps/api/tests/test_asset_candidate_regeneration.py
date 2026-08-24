import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from app.db.session import create_database_engine, initialize_database
from app.models import AssetCandidate, Character, Project, Shot
from app.providers.types import ProviderAsset, ProviderError, ProviderResponse, ProviderStatus
from app.services.asset_candidate_service import (
    delete_asset_candidate,
    list_asset_candidates,
    regenerate_asset_candidate,
)
from app.services.asset_repository import next_candidate_version
from app.services.image_provider_profile_service import ImageProviderSelection


class _ImageProvider:
    name = "qwen_image_musubi"
    model = "test-qwen-image"
    capabilities = ["text_to_image"]

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.requests = []

    def submit(self, request):
        self.requests.append(request)
        if self.fail:
            return ProviderResponse(
                status=ProviderStatus.FAILED,
                provider_task_id="provider-task-failed",
                error=ProviderError(
                    error_code="test_generation_failed",
                    error_message="test provider failed",
                ),
            )
        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id="provider-task-ok",
            assets=[
                ProviderAsset(
                    asset_type="image",
                    uri="data:image/png;base64,dGVzdA==",
                    mime_type="image/png",
                    width=1024,
                    height=576,
                )
            ],
        )


class AssetCandidateRegenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(
            f"sqlite+pysqlite:///{(Path(self.temp_dir.name) / 'candidate-regeneration.sqlite3').as_posix()}"
        )
        initialize_database(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _source_candidate(self, db):
        project = Project(title="候选重生成测试", style="动画风格")
        db.add(project)
        db.flush()
        character = Character(
            project_id=project.id,
            name="Tom",
            identity="小学生",
            appearance="黑色短发，蓝色T恤，卡其色短裤，红色书包",
            fixed_prompt=(
                "Tom，小学生，黑色短发，蓝色T恤，卡其色短裤，红色书包，"
                "稳定身份、体型比例、配色、默认服装和标志性特征，多镜头保持一致"
            ),
            asset_spec={"entity_kind": "human"},
            status="approved",
        )
        db.add(character)
        db.flush()
        candidate = AssetCandidate(
            project_id=project.id,
            candidate_type="generated",
            asset_type="image",
            asset_role="character_main_ref",
            entity_type="character",
            entity_id=character.id,
            variant_key="base",
            version=1,
            uri="data:image/png;base64,b2xk",
            mime_type="image/png",
            provider="qwen_image_musubi",
            model="test-qwen-image",
            prompt="旧提示词：禁止多视图拼贴、角色设定表、重复人物",
            negative_prompt="旧负面提示词",
            raw_response={
                "request_metadata": {
                    "image_provider_profile_id": "saved-qwen-profile",
                }
            },
            status="pending_review",
        )
        db.add(candidate)
        db.commit()
        db.refresh(candidate)
        return candidate

    def _selection(self, provider):
        return ImageProviderSelection(
            provider=provider,
            profile_id="saved-qwen-profile",
            default_params={"size": "1024x576", "steps": 8, "guidance_scale": 1.0},
            source="saved",
        )

    def test_success_replaces_pending_candidate_with_next_version(self):
        provider = _ImageProvider()
        with self.session_factory() as db:
            source = self._source_candidate(db)
            with (
                patch(
                    "app.services.image_generation_service.resolve_image_provider_selection",
                    return_value=self._selection(provider),
                ) as resolve_profile,
                patch(
                    "app.services.image_generation_service.materialize_data_uri",
                    side_effect=lambda _project_id, _candidate_id, uri, _mime_type: uri,
                ),
                patch("app.services.image_generation_service._image_consistency_check", return_value=None),
            ):
                replacement, task = regenerate_asset_candidate(db, source.id)

            db.refresh(source)
            self.assertEqual(source.status, "rejected")
            self.assertIn(replacement.id, source.review_note)
            self.assertEqual(replacement.status, "pending_review")
            self.assertEqual(replacement.version, 2)
            self.assertEqual(replacement.raw_response["regeneration"]["source_candidate_id"], source.id)
            self.assertEqual(task.input_payload["source_candidate_id"], source.id)
            resolve_profile.assert_called_once_with(db, "saved-qwen-profile")
            self.assertEqual(len(provider.requests), 1)
            prompt = provider.requests[0].prompt
            self.assertIn("视觉风格：", prompt)
            self.assertTrue(prompt.endswith(db.get(Character, source.entity_id).fixed_prompt))

    def test_failed_generation_keeps_source_candidate_pending(self):
        provider = _ImageProvider(fail=True)
        with self.session_factory() as db:
            source = self._source_candidate(db)
            with patch(
                "app.services.image_generation_service.resolve_image_provider_selection",
                return_value=self._selection(provider),
            ):
                with self.assertRaises(HTTPException):
                    regenerate_asset_candidate(db, source.id)

            db.refresh(source)
            self.assertEqual(source.status, "pending_review")
            self.assertIsNone(source.rejected_at)

    def test_shot_regeneration_uses_current_shot_negative_prompt(self):
        provider = _ImageProvider()
        with self.session_factory() as db:
            project = Project(title="镜头重生成测试", style="动画风格")
            db.add(project)
            db.flush()
            shot = Shot(
                project_id=project.id,
                shot_no=1,
                description="特写：手指靠近羽毛",
                image_prompt=(
                    "风格DNA：二维动画\n"
                    "唯一可见瞬间：手指靠近羽毛\n"
                    "角色身份：只显示手部\n"
                    "场景身份：背景虚化\n"
                    "道具身份：无"
                ),
                negative_prompt="当前镜头负面提示词，完整人物",
                status="approved",
            )
            db.add(shot)
            db.flush()
            source = AssetCandidate(
                project_id=project.id,
                candidate_type="generated",
                asset_type="image",
                asset_role="shot_storyboard",
                entity_type="shot",
                entity_id=shot.id,
                version=1,
                uri="data:image/png;base64,b2xk",
                mime_type="image/png",
                provider="qwen_image_musubi",
                model="test-qwen-image",
                prompt="旧镜头提示词",
                negative_prompt="旧候选负面提示词",
                raw_response={"request_metadata": {"image_provider_profile_id": "saved-qwen-profile"}},
                status="pending_review",
            )
            db.add(source)
            db.commit()

            with (
                patch(
                    "app.services.image_generation_service.resolve_image_provider_selection",
                    return_value=self._selection(provider),
                ),
                patch(
                    "app.services.image_generation_service.materialize_data_uri",
                    side_effect=lambda _project_id, _candidate_id, uri, _mime_type: uri,
                ),
                patch("app.services.image_generation_service._image_consistency_check", return_value=None),
            ):
                replacement, _task = regenerate_asset_candidate(db, source.id)

            self.assertEqual(provider.requests[0].negative_prompt, "当前镜头负面提示词，完整人物")
            self.assertEqual(replacement.negative_prompt, "当前镜头负面提示词，完整人物")

    def test_delete_hides_candidate_purges_media_and_reserves_version(self):
        with self.session_factory() as db:
            source = self._source_candidate(db)
            source.version = 2
            source.uri = "http://127.0.0.1:8000/storage/projects/test/candidate-v2.png"
            db.add(source)
            db.commit()
            original_uri = source.uri

            with patch("app.services.asset_candidate_service.unlink_local_storage_file") as unlink_media:
                delete_asset_candidate(db, source.id)

            db.refresh(source)
            self.assertEqual(source.status, "deleted")
            self.assertEqual(source.uri, f"deleted://asset-candidate/{source.id}")
            self.assertEqual(source.raw_response["deletion"]["media_policy"], "local_file_unlinked")
            unlink_media.assert_called_once_with(original_uri)
            self.assertEqual(list_asset_candidates(db, source.project_id), [])
            self.assertEqual(
                [candidate.id for candidate in list_asset_candidates(db, source.project_id, status_filter="deleted")],
                [source.id],
            )
            self.assertEqual(
                next_candidate_version(
                    db,
                    source.project_id,
                    source.asset_type,
                    source.entity_type,
                    source.entity_id,
                    source.asset_role,
                    source.variant_key,
                ),
                3,
            )

    def test_promoted_candidate_cannot_be_deleted_directly(self):
        with self.session_factory() as db:
            source = self._source_candidate(db)
            source.status = "promoted"
            db.add(source)
            db.commit()

            with self.assertRaises(HTTPException) as raised:
                delete_asset_candidate(db, source.id)

            self.assertEqual(raised.exception.status_code, 409)
            db.refresh(source)
            self.assertEqual(source.status, "promoted")


if __name__ == "__main__":
    unittest.main()
