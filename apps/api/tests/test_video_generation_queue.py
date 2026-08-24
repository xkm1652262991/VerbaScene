import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db.session import create_database_engine, initialize_database
from app.models import Asset, GenerationTask, Project, Shot
from app.providers.mock import MockVideoProvider
from app.agents.video_generation import shot_video_prompt
from app.services.asset_service import (
    create_single_shot_video_candidate_task,
    execute_single_shot_video_candidate_task,
)
from app.services.shot_service import update_shot_video_reference
from app.services.video_generation_queue_service import cancel_queued_video_generation_task


class VideoGenerationQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(
            f"sqlite+pysqlite:///{(Path(self.temp_dir.name) / 'video-queue.sqlite3').as_posix()}"
        )
        initialize_database(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _approved_shot(self, db, shot_no: int = 1) -> Shot:
        project = Project(title="视频队列测试", style="二维动画")
        db.add(project)
        db.flush()
        shot = Shot(
            project_id=project.id,
            shot_no=shot_no,
            description="角色走进房间",
            video_prompt="角色缓慢前进，镜头轻微推进",
            status="approved",
        )
        db.add(shot)
        db.flush()
        db.add(
            Asset(
                project_id=project.id,
                asset_type="image",
                asset_role="shot_storyboard",
                entity_type="shot",
                entity_id=shot.id,
                version=1,
                uri="data:image/png;base64,dGVzdA==",
                mime_type="image/png",
                status="approved",
                is_selected=True,
            )
        )
        db.commit()
        db.refresh(shot)
        return shot

    def test_create_returns_queued_task_without_calling_provider(self):
        with self.session_factory() as db:
            shot = self._approved_shot(db)
            task = create_single_shot_video_candidate_task(
                db,
                shot.id,
                duration_sec=4,
                video_prompt="人物抬头，镜头缓慢推进",
            )

            self.assertEqual(task.status, "queued")
            self.assertEqual(task.progress, 0)
            self.assertEqual(task.input_payload["shot_id"], shot.id)
            self.assertEqual(task.input_payload["shot_no"], shot.shot_no)
            self.assertEqual(task.input_payload["duration_mode"], "fixed")
            self.assertEqual(task.input_payload["duration_sec"], "4")
            self.assertIsNone(task.input_payload["image_asset_id"])
            self.assertEqual(task.input_payload["video_reference_source"], "none")

    def test_provider_duration_limit_blocks_queue_without_truncating_shot(self):
        limited_provider = MockVideoProvider()
        limited_provider.max_duration_sec = 5
        with self.session_factory() as db:
            shot = self._approved_shot(db)
            shot.duration_sec = 12
            db.commit()

            with (
                patch("app.services.video_generation_service.provider_registry.get", return_value=limited_provider),
                self.assertRaises(HTTPException) as raised,
            ):
                create_single_shot_video_candidate_task(db, shot.id)

            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(
                raised.exception.detail["code"],
                "video_duration_exceeds_provider_limit",
            )
            self.assertEqual(raised.exception.detail["shot_duration_sec"], 12)
            self.assertEqual(raised.exception.detail["max_duration_sec"], 5)
            self.assertEqual(
                db.scalar(select(func.count(GenerationTask.id))),
                0,
            )
            self.assertEqual(float(db.get(Shot, shot.id).duration_sec), 12)

    def test_smart_duration_is_default_and_actual_duration_replaces_plan(self):
        provider = MockVideoProvider()
        provider.name = "seedance2_api"
        provider.smart_duration = True
        provider.min_duration_sec = 4
        provider.max_duration_sec = 15
        with self.session_factory() as db:
            shot = self._approved_shot(db)
            self.assertIsNone(shot.duration_sec)
            with patch("app.services.video_generation_service.provider_registry.get", return_value=provider):
                task = create_single_shot_video_candidate_task(db, shot.id)
                result = execute_single_shot_video_candidate_task(db, task.id)

            self.assertIsNotNone(result)
            candidate, completed = result
            db.refresh(shot)
            self.assertEqual(task.input_payload["duration_mode"], "provider_auto")
            self.assertIsNone(task.input_payload["duration_sec"])
            self.assertEqual(candidate.raw_response["request_metadata"]["duration_request"]["provider_value"], -1)
            self.assertEqual(completed.status, "succeeded")
            self.assertEqual(float(shot.duration_sec), 4)
            self.assertEqual(shot.shot_card["segment_plan"]["duration_mode"], "provider_auto")
            self.assertEqual(shot.shot_card["segment_plan"]["actual_duration_sec"], "4")

    def test_smart_duration_prompt_ignores_legacy_time_hints(self):
        with self.session_factory() as db:
            shot = self._approved_shot(db)
            shot.video_prompt = None
            shot.shot_card = {
                "segment_plan": {"duration_mode": "provider_auto"},
                "motion_timing": {
                    "duration_rationale": "固定 8 秒",
                    "beats": [{"time": "0-3 秒", "action": "角色抬头"}],
                },
            }

            prompt = shot_video_prompt(shot)

            self.assertIn("角色抬头", prompt)
            self.assertNotIn("固定 8 秒", prompt)
            self.assertNotIn("0-3 秒", prompt)
            self.assertNotIn("目标时长", prompt)
            self.assertNotIn("时长策略", prompt)

    def test_waiting_task_can_be_removed_and_same_shot_requeued(self):
        with self.session_factory() as db:
            shot = self._approved_shot(db)
            first = create_single_shot_video_candidate_task(db, shot.id)
            cancelled = cancel_queued_video_generation_task(db, first.id)
            second = create_single_shot_video_candidate_task(db, shot.id)

            self.assertEqual(cancelled.status, "cancelled")
            self.assertEqual(second.status, "queued")
            self.assertNotEqual(first.id, second.id)

    def test_same_shot_cannot_be_added_twice_while_active(self):
        with self.session_factory() as db:
            shot = self._approved_shot(db)
            create_single_shot_video_candidate_task(db, shot.id)

            with self.assertRaises(HTTPException) as raised:
                create_single_shot_video_candidate_task(db, shot.id)

            self.assertEqual(raised.exception.status_code, 409)

    def test_current_shot_does_not_need_review_approval_to_enter_queue(self):
        with self.session_factory() as db:
            shot = self._approved_shot(db)
            shot.status = "draft"
            db.commit()

            task = create_single_shot_video_candidate_task(db, shot.id)

            self.assertEqual(task.status, "queued")
            self.assertEqual(task.input_payload["shot_id"], shot.id)

    def test_manual_video_reference_is_saved_and_used_as_real_input_image(self):
        with self.session_factory() as db:
            shot = self._approved_shot(db)
            manual_reference = Asset(
                project_id=shot.project_id,
                asset_type="image",
                asset_role="scene_ref",
                entity_type="scene",
                entity_id="scene-1",
                version=1,
                uri="data:image/png;base64,bWFudWFs",
                mime_type="image/png",
                status="approved",
                is_selected=True,
            )
            db.add(manual_reference)
            db.commit()
            db.refresh(manual_reference)

            updated_shot = update_shot_video_reference(db, shot.id, manual_reference.id)
            task = create_single_shot_video_candidate_task(db, shot.id)

            self.assertEqual(updated_shot.shot_card["video_reference_asset_id"], manual_reference.id)
            self.assertEqual(task.input_payload["image_asset_id"], manual_reference.id)
            self.assertEqual(task.input_payload["video_reference_source"], "manual")


if __name__ == "__main__":
    unittest.main()
