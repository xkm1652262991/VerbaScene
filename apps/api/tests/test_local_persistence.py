import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.db.session import create_database_engine, initialize_database
from app.db.json_payload import compact_json_payload
from app.models import GenerationTask, Project
from app.services.task_service import list_generation_task_progress, mark_task_running, update_task_progress


class LocalPersistenceTests(unittest.TestCase):
    def test_default_database_is_project_local_sqlite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(
                _env_file=None,
                persistence_mode="local",
                database_url=None,
                local_data_dir=temp_dir,
            )

            self.assertEqual(settings.persistence_mode, "local")
            self.assertEqual(
                settings.effective_database_url,
                f"sqlite+pysqlite:///{Path(temp_dir).resolve().as_posix()}/content.sqlite3",
            )

    def test_explicit_database_url_keeps_database_mode(self):
        settings = Settings(
            _env_file=None,
            persistence_mode="database",
            database_url="postgresql+psycopg://user:password@localhost/example",
        )

        self.assertEqual(settings.persistence_mode, "database")
        self.assertEqual(
            settings.effective_database_url,
            "postgresql+psycopg://user:password@localhost/example",
        )

    def test_sqlite_schema_persists_json_and_enables_foreign_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "content.sqlite3"
            engine = create_database_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
            initialize_database(engine)
            session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

            with session_factory() as db:
                project = Project(
                    title="本地联调项目",
                    director_memory={"persistence": "local"},
                )
                db.add(project)
                db.commit()
                project_id = project.id

            with session_factory() as db:
                loaded = db.scalar(select(Project).where(Project.id == project_id))
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded.director_memory, {"persistence": "local"})
                self.assertEqual(db.scalar(text("PRAGMA foreign_keys")), 1)

            engine.dispose()

    def test_local_task_payload_drops_inline_binary_but_keeps_trace_fields(self):
        inline_image = "data:image/png;base64," + ("A" * 20_000)
        compacted = compact_json_payload(
            {
                "asset_id": "asset-1",
                "reference": {"uri": inline_image},
                "prompt": "保留这段提示词",
            }
        )

        self.assertEqual(compacted["asset_id"], "asset-1")
        self.assertEqual(compacted["prompt"], "保留这段提示词")
        self.assertIn("omitted inline binary", compacted["reference"]["uri"])

        task = GenerationTask(
            project_id="project-1",
            task_type="local-payload-test",
            input_payload={"uri": inline_image},
        )
        self.assertIn("omitted inline binary", task.input_payload["uri"])

    def test_running_task_progress_is_visible_to_a_separate_reader(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "task-progress.sqlite3"
            engine = create_database_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
            initialize_database(engine)
            session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

            with session_factory() as writer:
                project = Project(title="真实进度测试")
                writer.add(project)
                writer.flush()
                task = GenerationTask(
                    project_id=project.id,
                    task_type="reference_image_candidate_generation",
                    status="queued",
                )
                writer.add(task)
                writer.commit()
                task_id = task.id

                mark_task_running(writer, task, "任务已开始", 5)
                with session_factory() as reader:
                    visible_task = reader.get(GenerationTask, task_id)
                    self.assertIsNotNone(visible_task)
                    self.assertEqual(visible_task.status, "running")
                    self.assertEqual(visible_task.progress, 5)
                    self.assertEqual(visible_task.progress_label, "任务已开始")

                update_task_progress(writer, task, "正在生成场景参考图候选：教室", 42)
                with session_factory() as reader:
                    visible_task = reader.get(GenerationTask, task_id)
                    self.assertIsNotNone(visible_task)
                    self.assertEqual(visible_task.progress, 42)
                    self.assertEqual(visible_task.progress_label, "正在生成场景参考图候选：教室")

                    progress_items, total = list_generation_task_progress(reader, project_id=project.id, limit=20)
                    self.assertEqual(total, 1)
                    self.assertEqual(progress_items[0]["id"], task_id)
                    self.assertEqual(progress_items[0]["progress"], 42)
                    self.assertNotIn("raw_response", progress_items[0])
                    self.assertNotIn("result_payload", progress_items[0])

            engine.dispose()


if __name__ == "__main__":
    unittest.main()
