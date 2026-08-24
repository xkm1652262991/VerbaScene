import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.session import create_database_engine, initialize_database
from app.models import Chapter, GenerationTask, Project
from app.schemas.project import ProjectDeleteRequest
from app.services.project_service import delete_project, get_project_deletion_preview


class ProjectDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "content.sqlite3"
        self.engine = create_database_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
        initialize_database(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self.original_storage_root = settings.storage_root
        settings.storage_root = self.temp_dir.name

    def tearDown(self):
        settings.storage_root = self.original_storage_root
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_delete_project_cascades_records_and_moves_storage_to_trash(self):
        with self.session_factory() as db:
            project = Project(title="待删除项目", director_memory={})
            db.add(project)
            db.flush()
            chapter = Chapter(project=project, outline="大纲", source_text="正文", source_word_count=2)
            task = GenerationTask(
                project_id=project.id,
                task_type="deletion-test",
                status="succeeded",
            )
            db.add_all([chapter, task])
            db.commit()
            project_id = project.id

        project_dir = Path(self.temp_dir.name) / "projects" / project_id
        media_path = project_dir / "assets" / "frame.png"
        media_path.parent.mkdir(parents=True)
        media_path.write_bytes(b"test-media")

        with self.session_factory() as db:
            preview = get_project_deletion_preview(db, project_id)
            self.assertTrue(preview["can_delete"])
            self.assertEqual(preview["record_counts"]["chapters"], 1)
            self.assertEqual(preview["record_counts"]["generation_tasks"], 1)
            self.assertEqual(preview["storage_file_count"], 1)
            self.assertEqual(preview["storage_bytes"], len(b"test-media"))

            result = delete_project(
                db,
                project_id,
                ProjectDeleteRequest(confirmation_title="待删除项目"),
            )

        self.assertEqual(result["storage_action"], "moved_to_trash")
        self.assertFalse(project_dir.exists())
        trashed_media = Path(self.temp_dir.name) / result["storage_trash_path"] / "assets" / "frame.png"
        self.assertEqual(trashed_media.read_bytes(), b"test-media")

        with self.session_factory() as db:
            self.assertIsNone(db.get(Project, project_id))
            self.assertEqual(db.scalar(select(func.count(Chapter.id)).where(Chapter.project_id == project_id)), 0)
            self.assertEqual(
                db.scalar(select(func.count(GenerationTask.id)).where(GenerationTask.project_id == project_id)),
                0,
            )

    def test_delete_project_rejects_wrong_title_and_active_task(self):
        with self.session_factory() as db:
            project = Project(title="运行中项目", director_memory={})
            db.add(project)
            db.flush()
            db.add(GenerationTask(project_id=project.id, task_type="deletion-test", status="running"))
            db.commit()
            project_id = project.id

            preview = get_project_deletion_preview(db, project_id)
            self.assertFalse(preview["can_delete"])
            self.assertEqual(preview["active_task_count"], 1)

            with self.assertRaises(HTTPException) as mismatch_error:
                delete_project(
                    db,
                    project_id,
                    ProjectDeleteRequest(confirmation_title="名字不对"),
                )
            self.assertEqual(mismatch_error.exception.status_code, 422)

            with self.assertRaises(HTTPException) as blocked_error:
                delete_project(
                    db,
                    project_id,
                    ProjectDeleteRequest(confirmation_title="运行中项目"),
                )
            self.assertEqual(blocked_error.exception.status_code, 409)

        with self.session_factory() as db:
            self.assertIsNotNone(db.get(Project, project_id))


if __name__ == "__main__":
    unittest.main()
