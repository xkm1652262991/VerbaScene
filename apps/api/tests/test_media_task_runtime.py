from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.assets.frame_extraction_tasks import (
    VideoFrameExtractionTaskHandler,
    create_video_frame_extraction_task,
)
from app.db.session import create_database_engine, initialize_database
from app.exports.export_tasks import create_project_export_task
from app.exports.task_handler import ProjectExportTaskHandler
from app.models import Asset, AssetCandidate, Export, GenerationTask, Project, Shot
from app.platform.media import get_media_store
from app.platform.media.subprocess_runner import MediaProcessResult
from app.platform.tasks.types import TaskStatus


class MediaTaskRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_root = Path(self.temp_dir.name) / "storage"
        self.engine = create_database_engine(
            f"sqlite+pysqlite:///{(Path(self.temp_dir.name) / 'media-runtime.sqlite3').as_posix()}"
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

    def _project_with_selected_video(self) -> tuple[str, str, str]:
        with (
            patch("app.core.config.settings.storage_root", str(self.storage_root)),
            patch("app.core.config.settings.public_storage_base_url", "/storage"),
        ):
            source_file = Path(self.temp_dir.name) / "source.mp4"
            source_file.write_bytes(b"video-source")
            with self.session_factory() as db:
                project = Project(title="媒体任务", resolution="854x480")
                db.add(project)
                db.flush()
                shot = Shot(
                    project_id=project.id,
                    shot_no=1,
                    description="Tiny waves",
                    duration_sec=Decimal("4"),
                    status="approved",
                )
                db.add(shot)
                db.flush()
                uri = get_media_store().put_file(
                    project.id,
                    source_file,
                    namespace="assets/videos",
                    filename="source.mp4",
                )
                asset = Asset(
                    project_id=project.id,
                    asset_type="video",
                    asset_role="shot_video",
                    entity_type="shot",
                    entity_id=shot.id,
                    version=1,
                    uri=uri,
                    mime_type="video/mp4",
                    duration_sec=Decimal("4"),
                    status="approved",
                    is_selected=True,
                )
                db.add(asset)
                db.commit()
                return project.id, shot.id, asset.id

    def test_export_request_only_queues_then_handler_persists_result(self):
        project_id, _shot_id, _asset_id = self._project_with_selected_video()
        with self.session_factory() as db:
            task = create_project_export_task(db, project_id, subtitle_mode="bilingual")
            task_id = task.id
            self.assertEqual(task.status, TaskStatus.QUEUED.value)
            self.assertEqual(task.input_payload["subtitle_mode"], "bilingual")

        def fake_build(_snapshot, output_path, **_kwargs):
            return ["ffmpeg", "-y", str(output_path)], {"shots": [], "subtitle_mode": "bilingual"}

        def fake_run(args, **_kwargs):
            Path(args[-1]).write_bytes(b"export-result")
            return MediaProcessResult(returncode=0, output_tail="")

        with (
            patch("app.exports.task_handler.SessionLocal", self.session_factory),
            patch("app.exports.task_handler.build_export_ffmpeg_args_from_snapshot", side_effect=fake_build),
            patch("app.exports.task_handler.run_cancellable_media_process", side_effect=fake_run),
            patch("app.core.config.settings.storage_root", str(self.storage_root)),
            patch("app.core.config.settings.public_storage_base_url", "/storage"),
        ):
            ProjectExportTaskHandler().execute(task_id)

        with self.session_factory() as db:
            task = db.get(GenerationTask, task_id)
            export = db.scalar(select(Export).where(Export.project_id == project_id))
            asset = db.get(Asset, task.output_asset_ids[0])
            self.assertEqual(task.status, TaskStatus.SUCCEEDED.value)
            self.assertEqual(task.result_payload["export_id"], export.id)
            self.assertEqual(asset.source_task_id, task_id)
            self.assertTrue(asset.uri.endswith(f"/{task_id}.mp4"))

    def test_frame_request_only_queues_then_handler_persists_candidate(self):
        _project_id, shot_id, asset_id = self._project_with_selected_video()
        with self.session_factory() as db:
            task = create_video_frame_extraction_task(
                db,
                asset_id,
                time_sec=Decimal("1.5"),
            )
            task_id = task.id
            self.assertEqual(task.status, TaskStatus.QUEUED.value)
            self.assertEqual(task.input_payload["time_sec"], "1.5")

        def fake_run(args, **_kwargs):
            Image.new("RGB", (64, 36), (120, 80, 30)).save(args[-1])
            return MediaProcessResult(returncode=0, output_tail="")

        with (
            patch("app.assets.frame_extraction_tasks.SessionLocal", self.session_factory),
            patch("app.assets.frame_extraction_tasks.run_cancellable_media_process", side_effect=fake_run),
            patch("app.core.config.settings.storage_root", str(self.storage_root)),
            patch("app.core.config.settings.public_storage_base_url", "/storage"),
        ):
            VideoFrameExtractionTaskHandler().execute(task_id)

        with self.session_factory() as db:
            task = db.get(GenerationTask, task_id)
            candidate = db.scalar(
                select(AssetCandidate).where(AssetCandidate.source_task_id == task_id)
            )
            self.assertEqual(task.status, TaskStatus.SUCCEEDED.value)
            self.assertEqual(candidate.entity_id, shot_id)
            self.assertEqual(candidate.width, 64)
            self.assertEqual(candidate.height, 36)
            self.assertTrue(candidate.uri.endswith(f"/{task_id}.png"))


if __name__ == "__main__":
    unittest.main()
