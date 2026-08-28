from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from app.db.session import create_database_engine, initialize_database
from app.main import app
from app.models import Character, Dialogue, Prop, Scene, Script, Shot
from app.platform.tasks.runtime import LocalTaskRuntime
from app.platform.tasks.types import TaskExecutionError
from app.production.shot_direction.contracts import SHOT_DIRECTION_PIPELINE_VERSION
from app.production.shot_direction.pipeline import ShotDirectionPipelineFailure
from app.production.shot_direction.service import (
    create_shot_direction_task,
    execute_shot_direction_task,
)
from app.providers.mock import MockLLMProvider
from app.providers.types import ProviderError, ProviderResponse, ProviderStatus, SubmissionState
from app.schemas.project import ProjectCreate
from app.services.dialogue_service import synchronize_script_dialogues
from app.services.project_service import create_project
from app.services.task_command_service import request_task_cancel, retry_task


class RecordingShotProvider(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.phases: list[str] = []

    def submit(self, request):
        self.phases.append(str(request.metadata.get("phase") or ""))
        return super().submit(request)


class InvalidShotDraftProvider(RecordingShotProvider):
    def submit(self, request):
        response = super().submit(request)
        if request.metadata.get("phase") in {"storyboard_draft", "storyboard_structure_recovery"}:
            return ProviderResponse(
                status=ProviderStatus.SUCCEEDED,
                provider_task_id=response.provider_task_id,
                raw_response={"text": "{}"},
            )
        return response


class ReviewUnavailableProvider(RecordingShotProvider):
    def submit(self, request):
        response = super().submit(request)
        if request.metadata.get("phase") == "storyboard_reflection":
            return ProviderResponse(
                status=ProviderStatus.FAILED,
                provider_task_id=response.provider_task_id,
                error=ProviderError(
                    error_code="review_unavailable",
                    error_message="review service unavailable",
                    submission_state=SubmissionState.ACCEPTED,
                ),
            )
        return response


class RetryOnceShotDraftProvider(RecordingShotProvider):
    def __init__(self) -> None:
        super().__init__()
        self.draft_attempts = 0

    def submit(self, request):
        phase = str(request.metadata.get("phase") or "")
        self.phases.append(phase)
        if phase == "storyboard_draft":
            self.draft_attempts += 1
            if self.draft_attempts == 1:
                return ProviderResponse(
                    status=ProviderStatus.FAILED,
                    provider_task_id="",
                    error=ProviderError(
                        error_code="provider_temporarily_unavailable",
                        error_message="request rejected before acceptance",
                        is_retryable=True,
                        submission_state=SubmissionState.NOT_SUBMITTED,
                    ),
                )
        return MockLLMProvider.submit(self, request)


class ShotDirectionQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(
            f"sqlite+pysqlite:///{(Path(self.temp_dir.name) / 'shot-direction.sqlite3').as_posix()}"
        )
        initialize_database(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _project_with_inputs(self, db):
        project = create_project(
            db,
            ProjectCreate(
                title="一起收拾玩具",
                input_mode="imported_script",
                source_text="Mia asks Leo for help and they put the blocks away.",
                target_duration_sec=12,
            ),
        )
        chapter = project.chapters[0]
        character = Character(
            project_id=project.id,
            name="Mia",
            identity="主动邀请朋友合作的小学生",
            appearance="黄色上衣、蓝色背带裤",
            asset_spec={"state_variants": [{"key": "holding-block", "description": "拿起红色积木"}]},
            status="ready_for_review",
        )
        scene = Scene(
            project_id=project.id,
            name="活动室",
            description="明亮的儿童活动室",
            asset_spec={},
            status="ready_for_review",
        )
        prop = Prop(
            project_id=project.id,
            name="红色积木",
            description="散落在地面的红色积木",
            asset_spec={},
            status="ready_for_review",
        )
        db.add_all([character, scene, prop])
        db.flush()
        script = Script(
            project_id=project.id,
            chapter_id=chapter.id,
            version=1,
            content="Mia请朋友一起收拾积木。",
            scenes=[
                {
                    "scene_no": 1,
                    "title": "一起收拾",
                    "location": "活动室",
                    "characters": ["Mia"],
                    "props": ["红色积木"],
                    "visible_action": "Mia捡起积木并请朋友帮忙。",
                    "start_state": "积木散落。",
                    "end_state": "积木被收好。",
                    "dialogues": [
                        {
                            "speaker": "Mia",
                            "text": "Can you help me?",
                            "translation_zh": "你能帮我吗？",
                            "emotion": "友好",
                            "sound_cues": ["积木轻碰声"],
                        }
                    ],
                }
            ],
            dialogues=[],
            status="ready_for_review",
        )
        db.add(script)
        db.flush()
        synchronize_script_dialogues(db, script)
        db.commit()
        return project, script, character, scene, prop

    def _old_shot(self, db, project, script, scene):
        old = Shot(
            project_id=project.id,
            script_id=script.id,
            scene_id=scene.id,
            shot_no=1,
            shot_batch_id="old-batch",
            is_current=True,
            description="旧片段",
            character_ids=[],
            prop_ids=[],
            dialogue_ids=[],
            shot_card={},
            generation_mode="image_to_video",
            status="ready_for_review",
        )
        db.add(old)
        db.commit()
        return old

    def test_openapi_exposes_shot_direction_as_accepted_background_task(self):
        operation = app.openapi()["paths"]["/api/projects/{project_id}/shots/generate"]["post"]

        self.assertIn("202", operation["responses"])
        response_schema = operation["responses"]["202"]["content"]["application/json"]["schema"]
        self.assertIn("GenerationTaskRead", json.dumps(response_schema))
        self.assertTrue(any(item["name"] == "Idempotency-Key" for item in operation["parameters"]))

    def test_creation_freezes_inputs_and_enforces_idempotency_and_active_dedupe(self):
        provider = RecordingShotProvider()
        with self.session_factory() as db:
            project, script, character, scene, _prop = self._project_with_inputs(db)
            with patch("app.production.shot_direction.service.provider_registry.get", return_value=provider):
                task = create_shot_direction_task(db, project.id, idempotency_key="shot-request-1")
                same = create_shot_direction_task(db, project.id, idempotency_key="shot-request-1")
                with self.assertRaises(HTTPException) as duplicate:
                    create_shot_direction_task(db, project.id, idempotency_key="shot-request-2")

            self.assertEqual(task.id, same.id)
            self.assertEqual(task.status, "queued")
            self.assertEqual(task.resource_key, f"project:{project.id}:shots")
            self.assertEqual(provider.phases, [])
            self.assertEqual(duplicate.exception.status_code, 409)
            snapshot = task.input_payload["shot_direction_input"]
            self.assertEqual(snapshot["script_id"], script.id)
            self.assertEqual(snapshot["characters"][0]["id"], character.id)
            self.assertEqual(snapshot["scenes"][0]["id"], scene.id)
            self.assertEqual(snapshot["characters"][0]["asset_spec"]["state_variants"][0]["key"], "holding-block")
            self.assertEqual(task.raw_response["checkpoint"]["asset_report"]["inspection_level"], "metadata_only")
            self.assertFalse(task.raw_response["checkpoint"]["asset_report"]["generation_ready"])

            script.content = "排队期间被修改的文本"
            db.add(script)
            db.commit()
            self.assertNotEqual(task.input_payload["shot_direction_input"]["script_content"], script.content)

    def test_success_atomically_switches_batch_and_returns_normalized_report(self):
        provider = RecordingShotProvider()
        with self.session_factory() as db:
            project, script, _character, scene, _prop = self._project_with_inputs(db)
            old = self._old_shot(db, project, script, scene)
            with patch("app.production.shot_direction.service.provider_registry.get", return_value=provider):
                task = create_shot_direction_task(db, project.id)
                shots, completed = execute_shot_direction_task(db, task.id)

            db.refresh(old)
            self.assertFalse(old.is_current)
            self.assertTrue(shots)
            self.assertTrue(all(shot.is_current and shot.shot_batch_id == task.id for shot in shots))
            self.assertEqual(completed.status, "succeeded")
            self.assertEqual(completed.result_payload["pipeline_version"], SHOT_DIRECTION_PIPELINE_VERSION)
            self.assertEqual(completed.result_payload["inspection_level"], "metadata_only")
            self.assertIn(completed.result_payload["quality_gate"], {"pass", "needs_attention"})
            self.assertIn("director_report", completed.result_payload)
            self.assertEqual(
                provider.phases,
                ["storyboard_draft", "storyboard_reflection"],
            )
            dialogue = db.query(Dialogue).filter(Dialogue.script_id == script.id).one()
            self.assertIn(dialogue.id, shots[0].dialogue_ids)
            self.assertEqual(dialogue.shot_id, shots[0].id)

    def test_invalid_final_draft_keeps_current_batch(self):
        provider = InvalidShotDraftProvider()
        with self.session_factory() as db:
            project, script, _character, scene, _prop = self._project_with_inputs(db)
            old = self._old_shot(db, project, script, scene)
            with patch("app.production.shot_direction.service.provider_registry.get", return_value=provider):
                task = create_shot_direction_task(db, project.id)
                with self.assertRaises(ShotDirectionPipelineFailure):
                    execute_shot_direction_task(db, task.id)

            db.refresh(task)
            db.refresh(old)
            self.assertEqual(task.status, "failed")
            self.assertTrue(old.is_current)
            self.assertEqual(provider.phases, ["storyboard_draft", "storyboard_structure_recovery"])

    def test_review_failure_saves_valid_draft_as_review_unavailable(self):
        provider = ReviewUnavailableProvider()
        with self.session_factory() as db:
            project, _script, _character, _scene, _prop = self._project_with_inputs(db)
            with patch("app.production.shot_direction.service.provider_registry.get", return_value=provider):
                task = create_shot_direction_task(db, project.id)
                shots, completed = execute_shot_direction_task(db, task.id)

            self.assertTrue(shots)
            self.assertEqual(completed.status, "succeeded")
            self.assertEqual(completed.result_payload["quality_gate"], "review_unavailable")
            self.assertFalse(completed.raw_response["checkpoint"]["review_available"])
            self.assertIn(
                "keep_valid_draft",
                {item["action"] for item in completed.raw_response["checkpoint"]["pipeline_trace"]["fallbacks"]},
            )

    def test_not_submitted_draft_is_retried_once(self):
        provider = RetryOnceShotDraftProvider()
        with self.session_factory() as db:
            project, _script, _character, _scene, _prop = self._project_with_inputs(db)
            with patch("app.production.shot_direction.service.provider_registry.get", return_value=provider):
                task = create_shot_direction_task(db, project.id)
                with self.assertRaises(TaskExecutionError) as raised:
                    execute_shot_direction_task(db, task.id)
            self.assertEqual(raised.exception.submission_state, SubmissionState.NOT_SUBMITTED)
            runtime = LocalTaskRuntime(session_factory=self.session_factory)
            runtime._handle_execution_error(task.id, "test-worker", raised.exception)
            db.refresh(task)
            self.assertEqual(task.status, "queued")
            self.assertEqual(task.retry_count, 1)

            with patch("app.production.shot_direction.service.provider_registry.get", return_value=provider):
                _shots, completed = execute_shot_direction_task(db, task.id)

            self.assertEqual(completed.status, "succeeded")
            self.assertEqual(provider.phases.count("storyboard_draft"), 2)

    def test_inflight_draft_is_not_repeated_after_restart(self):
        provider = RecordingShotProvider()
        with self.session_factory() as db:
            project, script, _character, scene, _prop = self._project_with_inputs(db)
            old = self._old_shot(db, project, script, scene)
            with patch("app.production.shot_direction.service.provider_registry.get", return_value=provider):
                task = create_shot_direction_task(db, project.id)
            checkpoint = json.loads(json.dumps(task.raw_response["checkpoint"]))
            checkpoint["inflight_phase"] = {
                "phase": "storyboard_draft",
                "attempted_at": datetime.now(timezone.utc).isoformat(),
            }
            task.status = "running"
            task.raw_response = {"checkpoint": checkpoint}
            db.add(task)
            db.commit()

            with patch("app.production.shot_direction.service.provider_registry.get", return_value=provider):
                with self.assertRaises(ShotDirectionPipelineFailure):
                    execute_shot_direction_task(db, task.id)

            db.refresh(task)
            db.refresh(old)
            self.assertEqual(provider.phases, [])
            self.assertEqual(task.error_code, "provider_submission_uncertain")
            self.assertTrue(old.is_current)

    def test_queued_cancel_and_manual_retry_reuse_the_original_snapshot(self):
        provider = RecordingShotProvider()
        with self.session_factory() as db:
            project, _script, _character, _scene, _prop = self._project_with_inputs(db)
            with patch("app.production.shot_direction.service.provider_registry.get", return_value=provider):
                cancelled = create_shot_direction_task(db, project.id)
            original_input = cancelled.input_payload
            request_task_cancel(db, cancelled)
            db.commit()
            self.assertEqual(cancelled.status, "cancelled")

            retry = retry_task(db, cancelled)
            db.commit()
            self.assertTrue(retry.created)
            self.assertEqual(retry.task.retry_of_task_id, cancelled.id)
            self.assertEqual(retry.task.input_payload, original_input)
            self.assertEqual(retry.task.resource_key, cancelled.resource_key)


if __name__ == "__main__":
    unittest.main()
