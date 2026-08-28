from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.assets.contracts import (
    IMAGE_CANDIDATE_TASK_TYPE,
    REFERENCE_IMAGE_BATCH_TASK_TYPE,
)
from app.assets.image_batch_coordinator import reconcile_image_batch
from app.assets.image_task_handler import ImageCandidateTaskHandler
from app.assets.image_tasks import (
    create_reference_image_batch_task,
    create_single_reference_image_task,
)
from app.db.session import create_database_engine, initialize_database
from app.models import (
    AssetCandidate,
    Character,
    GenerationTask,
    Project,
    ProjectStageRun,
    Scene,
)
from app.platform.tasks.repository import TaskRepository
from app.platform.tasks.types import TaskStatus
from app.providers.types import (
    ProviderAsset,
    ProviderExecutionMode,
    ProviderResponse,
    ProviderStatus,
)
from app.services.image_provider_profile_service import ImageProviderSelection
from app.services.task_command_service import request_task_cancel, retry_task
from app.services.workflow_state_service import mark_stage_failed


class _AsyncImageProvider:
    name = "async-image-test"
    model = "async-image-model"
    capabilities = ["text_to_image", "async", "poll"]

    def __init__(self) -> None:
        self.submit_count = 0
        self.poll_count = 0
        self.fetch_count = 0

    def submit(self, _request):
        self.submit_count += 1
        return ProviderResponse(
            status=ProviderStatus.QUEUED,
            provider_task_id="remote-image-1",
            execution_mode=ProviderExecutionMode.ASYNC,
            poll_after_sec=1,
        )

    def poll(self, provider_task_id: str):
        self.poll_count += 1
        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id=provider_task_id,
            execution_mode=ProviderExecutionMode.ASYNC,
        )

    def fetch_result(self, provider_task_id: str, **_kwargs):
        self.fetch_count += 1
        output = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        output.close()
        Image.new("RGB", (32, 18), (80, 120, 200)).save(output.name)
        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id=provider_task_id,
            execution_mode=ProviderExecutionMode.ASYNC,
            assets=[
                ProviderAsset(
                    asset_type="image",
                    uri=output.name,
                    mime_type="image/png",
                    width=32,
                    height=18,
                    metadata={"temporary_file": True},
                )
            ],
        )


class ImageTaskRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_root = Path(self.temp_dir.name) / "storage"
        self.engine = create_database_engine(
            f"sqlite+pysqlite:///{(Path(self.temp_dir.name) / 'image-runtime.sqlite3').as_posix()}"
        )
        initialize_database(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
        )

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _project_with_targets(self) -> tuple[str, str, str]:
        with self.session_factory() as db:
            project = Project(title="异步图片任务", style="二维儿童动画")
            db.add(project)
            db.flush()
            character = Character(
                project_id=project.id,
                name="Tiny",
                fixed_prompt="Tiny 的稳定多视角角色设定",
                asset_spec={},
                status="approved",
            )
            scene = Scene(
                project_id=project.id,
                name="Classroom",
                fixed_prompt="明亮的英语教室",
                asset_spec={},
                status="approved",
            )
            db.add_all([character, scene])
            db.commit()
            return project.id, character.id, scene.id

    @staticmethod
    def _selection(provider: _AsyncImageProvider) -> ImageProviderSelection:
        return ImageProviderSelection(
            provider=provider,
            profile_id="test-image-profile",
            default_params={"size": "1280x720"},
            source="saved",
        )

    def test_remote_id_is_persisted_and_recovery_only_polls(self):
        provider = _AsyncImageProvider()
        project_id, character_id, _scene_id = self._project_with_targets()
        with self.session_factory() as db, patch(
            "app.assets.image_tasks.resolve_image_provider_selection",
            return_value=self._selection(provider),
        ):
            task = create_single_reference_image_task(
                db,
                project_id,
                entity_type="character",
                entity_id=character_id,
                asset_role="character_main_ref",
                image_provider_profile_id="test-image-profile",
            )
            task_id = task.id
            self.assertEqual(task.status, TaskStatus.QUEUED.value)
            self.assertIn("provider_request", task.input_payload)

        handler = ImageCandidateTaskHandler()
        with (
            patch("app.assets.image_task_handler.SessionLocal", self.session_factory),
            patch(
                "app.assets.image_task_handler.resolve_image_provider_selection",
                return_value=self._selection(provider),
            ),
            patch("app.core.config.settings.storage_root", str(self.storage_root)),
            patch("app.core.config.settings.public_storage_base_url", "/storage"),
            patch("app.services.image_generation_service._image_consistency_check", return_value=None),
        ):
            handler.execute(task_id)
            with self.session_factory() as db:
                task = db.get(GenerationTask, task_id)
                self.assertEqual(task.status, TaskStatus.WAITING_PROVIDER.value)
                self.assertEqual(task.provider_task_id, "remote-image-1")
            handler.execute(task_id)

        self.assertEqual(provider.submit_count, 1)
        self.assertEqual(provider.poll_count, 1)
        self.assertEqual(provider.fetch_count, 1)
        with self.session_factory() as db:
            task = db.get(GenerationTask, task_id)
            candidate = db.scalar(
                select(AssetCandidate).where(AssetCandidate.source_task_id == task_id)
            )
            self.assertEqual(task.status, TaskStatus.SUCCEEDED.value)
            self.assertIsNotNone(candidate)
            self.assertTrue(candidate.uri.startswith("/storage/projects/"))

    def test_batch_creation_is_atomic_when_one_target_is_active(self):
        provider = _AsyncImageProvider()
        project_id, character_id, _scene_id = self._project_with_targets()
        with self.session_factory() as db, patch(
            "app.assets.image_tasks.resolve_image_provider_selection",
            return_value=self._selection(provider),
        ):
            active = create_single_reference_image_task(
                db,
                project_id,
                entity_type="character",
                entity_id=character_id,
                asset_role="character_main_ref",
                image_provider_profile_id="test-image-profile",
            )

        with self.session_factory() as db, patch(
            "app.assets.image_tasks.resolve_image_provider_selection",
            return_value=self._selection(provider),
        ):
            with self.assertRaises(HTTPException) as raised:
                create_reference_image_batch_task(
                    db,
                    project_id,
                    image_provider_profile_id="test-image-profile",
                )
            self.assertEqual(raised.exception.status_code, 409)
            self.assertIn(character_id, raised.exception.detail["conflict_target_ids"])

        with self.session_factory() as db:
            tasks = list(db.scalars(select(GenerationTask)).all())
            self.assertEqual([task.id for task in tasks], [active.id])

    def test_parent_aggregates_child_candidate_ids(self):
        provider = _AsyncImageProvider()
        project_id, _character_id, _scene_id = self._project_with_targets()
        with self.session_factory() as db, patch(
            "app.assets.image_tasks.resolve_image_provider_selection",
            return_value=self._selection(provider),
        ):
            parent = create_reference_image_batch_task(
                db,
                project_id,
                image_provider_profile_id="test-image-profile",
            )
            self.assertEqual(parent.task_type, REFERENCE_IMAGE_BATCH_TASK_TYPE)
            children = list(
                db.scalars(
                    select(GenerationTask).where(GenerationTask.parent_task_id == parent.id)
                ).all()
            )
            self.assertEqual(len(children), 2)
            self.assertTrue(all(child.task_type == IMAGE_CANDIDATE_TASK_TYPE for child in children))
            for index, child in enumerate(children, start=1):
                TaskRepository().finish(
                    db,
                    child,
                    status=TaskStatus.SUCCEEDED,
                    progress_label="done",
                    result_payload={"candidate_ids": [f"candidate-{index}"]},
                )
            reconcile_image_batch(db, parent)
            db.commit()
            self.assertEqual(parent.status, TaskStatus.SUCCEEDED.value)
            self.assertEqual(
                parent.result_payload["candidate_ids"],
                ["candidate-1", "candidate-2"],
            )

    def test_retrying_cancelled_child_reopens_parent_without_cancel_flag(self):
        provider = _AsyncImageProvider()
        project_id, _character_id, _scene_id = self._project_with_targets()
        with self.session_factory() as db, patch(
            "app.assets.image_tasks.resolve_image_provider_selection",
            return_value=self._selection(provider),
        ):
            parent = create_reference_image_batch_task(
                db,
                project_id,
                image_provider_profile_id="test-image-profile",
            )
            children = list(
                db.scalars(
                    select(GenerationTask).where(GenerationTask.parent_task_id == parent.id)
                ).all()
            )
            request_task_cancel(db, parent)
            reconcile_image_batch(db, parent)
            db.commit()
            self.assertEqual(parent.status, TaskStatus.CANCELLED.value)
            self.assertIsNotNone(parent.cancel_requested_at)

            result = retry_task(db, children[0])
            db.commit()
            db.refresh(parent)
            self.assertTrue(result.created)
            self.assertEqual(parent.status, TaskStatus.WAITING_CHILDREN.value)
            self.assertIsNone(parent.cancel_requested_at)

    def test_queued_image_cancel_closes_the_asset_workspace_run(self):
        provider = _AsyncImageProvider()
        project_id, character_id, _scene_id = self._project_with_targets()
        with self.session_factory() as db, patch(
            "app.assets.image_tasks.resolve_image_provider_selection",
            return_value=self._selection(provider),
        ):
            task = create_single_reference_image_task(
                db,
                project_id,
                entity_type="character",
                entity_id=character_id,
                asset_role="character_main_ref",
                image_provider_profile_id="test-image-profile",
            )
            request_task_cancel(db, task)
            db.commit()
            run = db.scalar(
                select(ProjectStageRun).where(ProjectStageRun.task_id == task.id)
            )
            self.assertEqual(task.status, TaskStatus.CANCELLED.value)
            self.assertEqual(run.stage, "assets")
            self.assertEqual(run.status, "cancelled")

    def test_concurrent_failure_closes_only_its_own_workspace_run(self):
        provider = _AsyncImageProvider()
        project_id, character_id, scene_id = self._project_with_targets()
        with self.session_factory() as db, patch(
            "app.assets.image_tasks.resolve_image_provider_selection",
            return_value=self._selection(provider),
        ):
            character_task = create_single_reference_image_task(
                db,
                project_id,
                entity_type="character",
                entity_id=character_id,
                asset_role="character_main_ref",
                image_provider_profile_id="test-image-profile",
            )
            scene_task = create_single_reference_image_task(
                db,
                project_id,
                entity_type="scene",
                entity_id=scene_id,
                asset_role="scene_ref",
                image_provider_profile_id="test-image-profile",
            )
            mark_stage_failed(
                db,
                project_id,
                "images",
                summary="character failed",
                task_id=character_task.id,
                error_code="test_failure",
            )
            db.commit()
            runs = {
                run.task_id: run
                for run in db.scalars(
                    select(ProjectStageRun).where(
                        ProjectStageRun.task_id.in_((character_task.id, scene_task.id))
                    )
                ).all()
            }
            self.assertEqual(runs[character_task.id].status, "failed")
            self.assertEqual(runs[scene_task.id].status, "running")


if __name__ == "__main__":
    unittest.main()
