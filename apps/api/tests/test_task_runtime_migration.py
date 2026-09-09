import json
from pathlib import Path
import tempfile
import unittest

from sqlalchemy import inspect, text

from app.db.session import create_database_engine, initialize_database


class TaskRuntimeMigrationTests(unittest.TestCase):
    def test_legacy_sqlite_is_backed_up_and_tasks_are_safely_normalized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "legacy.sqlite3"
            engine = create_database_engine(
                f"sqlite+pysqlite:///{database_path.as_posix()}"
            )
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "CREATE TABLE projects ("
                        "id VARCHAR(36) PRIMARY KEY, title VARCHAR(255), "
                        "created_at DATETIME, updated_at DATETIME)"
                    )
                )
                connection.execute(
                    text(
                        "CREATE TABLE generation_tasks ("
                        "id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL, "
                        "task_type VARCHAR(80) NOT NULL, provider_task_id VARCHAR(255), "
                        "input_payload JSON NOT NULL, status VARCHAR(40) NOT NULL, "
                        "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL, "
                        "FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO projects(id, title, created_at, updated_at) "
                        "VALUES ('project-1', 'legacy', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO generation_tasks("
                        "id, project_id, task_type, provider_task_id, input_payload, "
                        "status, created_at, updated_at) VALUES ("
                        "'task-1', 'project-1', 'single_shot_video_candidate_generation', "
                        "NULL, :payload, 'running', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {"payload": json.dumps({"shot_id": "shot-1"})},
                )
                for task_id in ("task-2", "task-3"):
                    connection.execute(
                        text(
                            "INSERT INTO generation_tasks("
                            "id, project_id, task_type, provider_task_id, input_payload, "
                            "status, created_at, updated_at) VALUES ("
                            ":task_id, 'project-1', 'single_shot_video_candidate_generation', "
                            "NULL, :payload, 'queued', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                        ),
                        {
                            "task_id": task_id,
                            "payload": json.dumps({"shot_id": "shot-2"}),
                        },
                    )

            initialize_database(engine)

            columns = {
                column["name"] for column in inspect(engine).get_columns("generation_tasks")
            }
            self.assertTrue(
                {
                    "parent_task_id",
                    "retry_of_task_id",
                    "resource_key",
                    "active_dedupe_key",
                    "available_at",
                    "lease_owner",
                    "lease_token",
                    "lease_expires_at",
                    "heartbeat_at",
                    "cancel_requested_at",
                }.issubset(columns)
            )
            self.assertIn(
                "completion_key",
                {column["name"] for column in inspect(engine).get_columns("assets")},
            )
            self.assertIn(
                "completion_key",
                {
                    column["name"]
                    for column in inspect(engine).get_columns("asset_candidates")
                },
            )
            with engine.connect() as connection:
                tasks = connection.execute(
                    text(
                        "SELECT id, task_type, resource_key, active_dedupe_key, "
                        "status, available_at, lease_expires_at "
                        "FROM generation_tasks ORDER BY id"
                    )
                ).mappings().all()
                project_count = connection.execute(
                    text("SELECT COUNT(*) FROM projects WHERE id = 'project-1'")
                ).scalar_one()
            self.assertEqual(project_count, 1)
            interrupted, queued, duplicate = tasks
            self.assertEqual(interrupted["task_type"], "shot_video_candidate_generation")
            self.assertEqual(interrupted["resource_key"], "shot:shot-1:video")
            self.assertEqual(interrupted["status"], "failed")
            self.assertIsNone(interrupted["active_dedupe_key"])
            self.assertIsNone(interrupted["lease_expires_at"])

            self.assertEqual(queued["status"], "queued")
            self.assertEqual(queued["resource_key"], "shot:shot-2:video")
            self.assertEqual(
                queued["active_dedupe_key"],
                "shot_video_candidate_generation:shot:shot-2:video",
            )
            self.assertIsNotNone(queued["available_at"])
            self.assertEqual(duplicate["status"], "failed")
            self.assertIsNone(duplicate["active_dedupe_key"])

            backups = list(
                Path(temp_dir).glob(
                    "legacy.sqlite3.pre-20260824_unified_task_runtime_v1-*.bak"
                )
            )
            self.assertEqual(len(backups), 1)
            backup_engine = create_database_engine(
                f"sqlite+pysqlite:///{backups[0].as_posix()}"
            )
            with backup_engine.connect() as connection:
                self.assertEqual(
                    connection.execute(
                        text("SELECT COUNT(*) FROM generation_tasks")
                    ).scalar_one(),
                    3,
                )
            backup_engine.dispose()

            initialize_database(engine)
            self.assertEqual(
                len(
                    list(
                        Path(temp_dir).glob(
                            "legacy.sqlite3.pre-20260824_unified_task_runtime_v1-*.bak"
                        )
                    )
                ),
                1,
            )
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
