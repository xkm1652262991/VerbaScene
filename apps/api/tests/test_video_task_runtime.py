from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db.session import create_database_engine, initialize_database
from app.models import Asset, AssetCandidate, GenerationTask, Project, Shot
from app.platform.tasks.repository import TaskConflictError, TaskRepository
from app.platform.tasks.types import TaskStatus
from app.production.video_task_handler import VideoCandidateTaskHandler
from app.production.video_candidate_persistence import persist_video_candidate_task_result
from app.production.video_tasks import (
    PROJECT_VIDEO_BATCH_TASK_TYPE,
    create_project_video_batch_task,
    create_video_candidate_task,
)
from app.production.contracts import VIDEO_CANDIDATE_TASK_TYPE
from app.providers.base import ProviderAdapter
from app.providers.types import (
    ProviderAsset,
    ProviderExecutionMode,
    ProviderRequest,
    ProviderResponse,
    ProviderStatus,
    ProviderType,
)


class AsyncVideoProvider(ProviderAdapter):
    name = "async-test"
    type = ProviderType.VIDEO
    model = "async-test-model"
    capabilities = ["async", "poll", "cancel"]
    smart_duration = False
    max_duration_sec = 30

    def __init__(self) -> None:
        self.submit_count = 0
        self.poll_count = 0
        self.fetch_count = 0
        self.cancelled: list[str] = []

    def submit(self, request: ProviderRequest) -> ProviderResponse:
        self.submit_count += 1
        return ProviderResponse(
            status=ProviderStatus.QUEUED,
            provider_task_id="remote-video-1",
            execution_mode=ProviderExecutionMode.ASYNC,
            poll_after_sec=1,
            raw_response={"id": "remote-video-1", "status": "queued"},
        )

    def poll(self, provider_task_id: str) -> ProviderResponse:
        self.poll_count += 1
        state = ProviderStatus.RUNNING if self.poll_count == 1 else ProviderStatus.SUCCEEDED
        return ProviderResponse(
            status=state,
            provider_task_id=provider_task_id,
            execution_mode=ProviderExecutionMode.ASYNC,
            poll_after_sec=1 if state == ProviderStatus.RUNNING else None,
            raw_response={"id": provider_task_id, "status": state.value},
        )

    def fetch_result(
        self,
        provider_task_id: str,
        *,
        request: ProviderRequest | None = None,
        provider_context: dict | None = None,
    ) -> ProviderResponse:
        _ = provider_context
        self.fetch_count += 1
        output = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        output.write(b"async-video")
        output.close()
        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id=provider_task_id,
            execution_mode=ProviderExecutionMode.ASYNC,
            assets=[
                ProviderAsset(
                    asset_type="video",
                    uri=output.name,
                    mime_type="video/mp4",
                    width=854,
                    height=480,
                    duration_sec=Decimal("4"),
                    metadata={"temporary_file": True},
                )
            ],
            raw_response={"status": "succeeded"},
        )

    def cancel(self, provider_task_id: str) -> None:
        self.cancelled.append(provider_task_id)


class VideoTaskRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "video-runtime.sqlite3"
        self.storage_root = Path(self.temp_dir.name) / "storage"
        self.engine = create_database_engine(
            f"sqlite+pysqlite:///{self.database_path.as_posix()}"
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

    def _project_with_shots(self, count: int = 1) -> tuple[str, list[str]]:
        with self.session_factory() as db:
            project = Project(title="异步视频任务", style="二维动画")
            db.add(project)
            db.flush()
            shots = []
            for index in range(count):
                shot = Shot(
                    project_id=project.id,
                    shot_no=index + 1,
                    description=f"镜头 {index + 1}",
                    video_prompt="角色挥手",
                    duration_sec=4,
                    status="approved",
                )
                db.add(shot)
                db.flush()
                shots.append(shot.id)
            db.commit()
            return project.id, shots

    def test_provider_id_is_persisted_and_restart_only_polls(self):
        provider = AsyncVideoProvider()
        _project_id, shot_ids = self._project_with_shots()
        with self.session_factory() as db, patch(
            "app.production.video_request_compiler.provider_registry.get",
            return_value=provider,
        ):
            task = create_video_candidate_task(db, shot_ids[0], duration_sec=Decimal("4"))
            task_id = task.id

        handler = VideoCandidateTaskHandler()
        with (
            patch("app.production.video_task_handler.SessionLocal", self.session_factory),
            patch("app.production.video_task_handler.provider_registry.get", return_value=provider),
            patch("app.core.config.settings.storage_root", str(self.storage_root)),
            patch("app.core.config.settings.public_storage_base_url", "/storage"),
        ):
            handler.execute(task_id)
            with self.session_factory() as db:
                submitted = db.get(GenerationTask, task_id)
                self.assertEqual(submitted.status, TaskStatus.WAITING_PROVIDER.value)
                self.assertEqual(submitted.provider_task_id, "remote-video-1")

            handler.execute(task_id)
            handler.execute(task_id)

        self.assertEqual(provider.submit_count, 1)
        self.assertEqual(provider.poll_count, 2)
        self.assertEqual(provider.fetch_count, 1)
        with self.session_factory() as db:
            task = db.get(GenerationTask, task_id)
            candidate = db.scalar(
                select(AssetCandidate).where(AssetCandidate.source_task_id == task_id)
            )
            asset = db.scalar(
                select(Asset)
                .where(Asset.source_task_id == task_id)
                .where(Asset.asset_type == "video")
            )
            self.assertEqual(task.status, TaskStatus.SUCCEEDED.value)
            self.assertIsNotNone(candidate)
            self.assertIsNotNone(asset)
            self.assertTrue(candidate.uri.startswith("/storage/projects/"))
            stored_path = self.storage_root / candidate.uri.removeprefix("/storage/")
            self.assertEqual(stored_path.read_bytes(), b"async-video")

            replayed = persist_video_candidate_task_result(
                db,
                task,
                ProviderResponse(
                    status=ProviderStatus.SUCCEEDED,
                    provider_task_id="remote-video-1",
                    assets=[
                        ProviderAsset(
                            asset_type="video",
                            uri="unused://duplicate-delivery",
                            mime_type="video/mp4",
                        )
                    ],
                ),
            )
            self.assertEqual(replayed.id, candidate.id)
            self.assertEqual(
                db.scalar(
                    select(func.count(AssetCandidate.id)).where(
                        AssetCandidate.source_task_id == task_id
                    )
                ),
                1,
            )
            self.assertEqual(
                db.scalar(
                    select(func.count(Asset.id)).where(Asset.source_task_id == task_id)
                ),
                1,
            )

    def test_legacy_queued_video_input_is_frozen_before_first_submit(self):
        provider = AsyncVideoProvider()
        project_id, shot_ids = self._project_with_shots()
        with self.session_factory() as db:
            task = TaskRepository().create(
                db,
                project_id=project_id,
                task_type=VIDEO_CANDIDATE_TASK_TYPE,
                resource_key=f"shot:{shot_ids[0]}:video",
                provider=provider.name,
                model=provider.model,
                input_payload={
                    "shot_id": shot_ids[0],
                    "duration_mode": "fixed",
                    "duration_sec": "4",
                    "video_prompt": "legacy snapshot",
                },
            ).task
            db.commit()
            task_id = task.id

        handler = VideoCandidateTaskHandler()
        with (
            patch("app.production.video_task_handler.SessionLocal", self.session_factory),
            patch("app.production.video_task_handler.provider_registry.get", return_value=provider),
            patch("app.production.video_request_compiler.provider_registry.get", return_value=provider),
        ):
            handler.execute(task_id)

        self.assertEqual(provider.submit_count, 1)
        with self.session_factory() as db:
            task = db.get(GenerationTask, task_id)
            self.assertEqual(task.status, TaskStatus.WAITING_PROVIDER.value)
            self.assertIn("provider_request", task.input_payload)
            self.assertEqual(task.input_payload["operation"], "legacy_migrated")
            self.assertEqual(
                task.input_payload["legacy_input_payload"]["video_prompt"],
                "legacy snapshot",
            )

    def test_batch_creation_is_atomic_and_reports_conflicting_shots(self):
        provider = AsyncVideoProvider()
        project_id, shot_ids = self._project_with_shots(2)
        with self.session_factory() as db, patch(
            "app.production.video_request_compiler.provider_registry.get",
            return_value=provider,
        ):
            active = create_video_candidate_task(db, shot_ids[0], update_shot=False)

        with self.session_factory() as db, patch(
            "app.production.video_request_compiler.provider_registry.get",
            return_value=provider,
        ):
            with self.assertRaises(HTTPException) as raised:
                create_project_video_batch_task(db, project_id)
            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(raised.exception.detail["conflict_shot_ids"], [shot_ids[0]])
            parents = list(
                db.scalars(
                    select(GenerationTask).where(
                        GenerationTask.task_type == PROJECT_VIDEO_BATCH_TASK_TYPE
                    )
                ).all()
            )
            self.assertEqual(parents, [])
            self.assertIsNotNone(db.get(GenerationTask, active.id))

    def test_waiting_provider_cancel_calls_remote_cancel_best_effort(self):
        provider = AsyncVideoProvider()
        _project_id, shot_ids = self._project_with_shots()
        with self.session_factory() as db, patch(
            "app.production.video_request_compiler.provider_registry.get",
            return_value=provider,
        ):
            task = create_video_candidate_task(db, shot_ids[0], update_shot=False)
            task_id = task.id
        handler = VideoCandidateTaskHandler()
        with (
            patch("app.production.video_task_handler.SessionLocal", self.session_factory),
            patch("app.production.video_task_handler.provider_registry.get", return_value=provider),
        ):
            handler.execute(task_id)
            with self.session_factory() as db:
                task = db.get(GenerationTask, task_id)
                TaskRepository().request_cancel(db, task)
                db.commit()
                self.assertEqual(task.status, TaskStatus.CANCELLING.value)
            handler.cancel(task_id)

        self.assertEqual(provider.cancelled, ["remote-video-1"])

    def test_parent_summary_uses_latest_manual_retry_attempt(self):
        provider = AsyncVideoProvider()
        project_id, _shot_ids = self._project_with_shots(2)
        repository = TaskRepository()
        with self.session_factory() as db, patch(
            "app.production.video_request_compiler.provider_registry.get",
            return_value=provider,
        ):
            parent = create_project_video_batch_task(db, project_id)
            children = list(
                db.scalars(
                    select(GenerationTask)
                    .where(GenerationTask.parent_task_id == parent.id)
                    .order_by(GenerationTask.created_at)
                ).all()
            )
            repository.finish(
                db,
                children[0],
                status=TaskStatus.SUCCEEDED,
                progress_label="done",
            )
            repository.finish(
                db,
                children[1],
                status=TaskStatus.FAILED,
                progress_label="failed",
                error_code="test",
                error_message="test",
            )
            repository.reconcile_parent(db, parent)
            self.assertEqual(parent.status, TaskStatus.FAILED.value)

            with self.assertRaises(TaskConflictError):
                repository.retry(
                    db,
                    parent,
                    allowed_task_types={PROJECT_VIDEO_BATCH_TASK_TYPE, VIDEO_CANDIDATE_TASK_TYPE},
                )

            retry = repository.retry(
                db,
                children[1],
                allowed_task_types={VIDEO_CANDIDATE_TASK_TYPE},
            ).task
            repository.finish(
                db,
                retry,
                status=TaskStatus.SUCCEEDED,
                progress_label="done",
            )
            repository.reconcile_parent(db, parent)
            db.commit()

            self.assertEqual(parent.status, TaskStatus.SUCCEEDED.value)
            self.assertEqual(parent.result_payload["summary"]["total"], 2)
            self.assertEqual(parent.result_payload["summary"]["attempt_count"], 3)
            self.assertEqual(parent.result_payload["summary"]["succeeded"], 2)

    def test_parent_cancel_cascades_to_queued_and_waiting_children(self):
        provider = AsyncVideoProvider()
        project_id, _shot_ids = self._project_with_shots(2)
        repository = TaskRepository()
        with self.session_factory() as db, patch(
            "app.production.video_request_compiler.provider_registry.get",
            return_value=provider,
        ):
            parent = create_project_video_batch_task(db, project_id)
            children = list(
                db.scalars(
                    select(GenerationTask)
                    .where(GenerationTask.parent_task_id == parent.id)
                    .order_by(GenerationTask.created_at)
                ).all()
            )
            children[1].status = TaskStatus.WAITING_PROVIDER.value
            children[1].provider_task_id = "remote-child"
            db.flush()

            repository.request_cancel(db, parent)
            db.commit()

            self.assertEqual(parent.status, TaskStatus.CANCELLING.value)
            self.assertEqual(children[0].status, TaskStatus.CANCELLED.value)
            self.assertEqual(children[1].status, TaskStatus.CANCELLING.value)
            self.assertIsNotNone(children[0].cancel_requested_at)
            self.assertIsNotNone(children[1].cancel_requested_at)


if __name__ == "__main__":
    unittest.main()
