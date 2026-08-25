from pathlib import Path
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from app.agents.script_contracts import SCRIPT_QUALITY_PIPELINE_VERSION
from app.agents.script_pipeline import ScriptPipelineFailure
from app.db.session import create_database_engine, initialize_database
from app.main import app
from app.models import AgentConfig
from app.platform.tasks.repository import TaskRepository
from app.platform.tasks.runtime import LocalTaskRuntime
from app.platform.tasks.types import TaskExecutionError
from app.providers.mock import MockLLMProvider
from app.providers.types import (
    ProviderError,
    ProviderResponse,
    ProviderStatus,
    SubmissionState,
)
from app.schemas.project import ProjectCreate
from app.services.project_service import create_project
from app.services.script_generation_queue_service import recover_script_generation_tasks
from app.scripts.service import create_script_generation_task, execute_script_generation_task


class RecordingMockLLMProvider(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.phases: list[str] = []

    def submit(self, request):
        self.phases.append(str(request.metadata.get("phase") or ""))
        return super().submit(request)


class ReviewUnavailableProvider(RecordingMockLLMProvider):
    def submit(self, request):
        response = super().submit(request)
        if request.metadata.get("phase") != "script_review":
            return response
        return ProviderResponse(
            status=ProviderStatus.FAILED,
            provider_task_id=response.provider_task_id,
            error=ProviderError(error_code="review_unavailable", error_message="review service unavailable"),
        )


class InvalidPatchProvider(RecordingMockLLMProvider):
    def submit(self, request):
        response = super().submit(request)
        phase = str(request.metadata.get("phase") or "")
        if phase == "script_review":
            return ProviderResponse(
                status=ProviderStatus.SUCCEEDED,
                provider_task_id=response.provider_task_id,
                raw_response={
                    "text": json.dumps(
                        {
                            "review": {
                                "issues": [
                                    {
                                        "code": "PAYOFF_MISSING",
                                        "category": "causality",
                                        "severity": "must_fix",
                                        "scene_nos": [1],
                                        "problem": "完成整理后没有反应。",
                                        "evidence": "visible_action 停在盒盖合上。",
                                        "repair_instruction": "补充两人的可见反应。",
                                        "protected_elements": ["请求与回应对白"],
                                    }
                                ],
                            }
                        },
                        ensure_ascii=False,
                    )
                },
            )
        if phase == "script_patch":
            return ProviderResponse(
                status=ProviderStatus.SUCCEEDED,
                provider_task_id=response.provider_task_id,
                raw_response={"text": json.dumps({"scenes": []})},
            )
        return response


class InvalidDraftProvider(RecordingMockLLMProvider):
    def submit(self, request):
        response = super().submit(request)
        if request.metadata.get("phase") not in {"script_draft", "structure_recovery"}:
            return response
        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id=response.provider_task_id,
            raw_response={"text": "{}"},
        )


class RetryOnceDraftProvider(RecordingMockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.draft_attempts = 0

    def submit(self, request):
        phase = str(request.metadata.get("phase") or "")
        self.phases.append(phase)
        if phase == "script_draft":
            self.draft_attempts += 1
            if self.draft_attempts == 1:
                return ProviderResponse(
                    status=ProviderStatus.FAILED,
                    provider_task_id="",
                    error=ProviderError(
                        error_code="provider_temporarily_unavailable",
                        error_message="request was rejected before acceptance",
                        is_retryable=True,
                        submission_state=SubmissionState.NOT_SUBMITTED,
                    ),
                )
        return MockLLMProvider.submit(self, request)


class ScriptGenerationQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(
            f"sqlite+pysqlite:///{(Path(self.temp_dir.name) / 'script-queue.sqlite3').as_posix()}"
        )
        initialize_database(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _project(self, db):
        return create_project(
            db,
            ProjectCreate(
                title="一起收拾玩具",
                input_mode="ai_brief",
                outline="Mia asks Leo to help put red blocks in a box.",
            ),
        )

    def test_openapi_exposes_script_generation_as_accepted_background_task(self):
        operation = app.openapi()["paths"]["/api/projects/{project_id}/script/generate"]["post"]

        self.assertIn("202", operation["responses"])
        response_schema = operation["responses"]["202"]["content"]["application/json"]["schema"]
        self.assertIn("GenerationTaskRead", json.dumps(response_schema))

    def test_task_is_persisted_before_any_model_call(self):
        provider = RecordingMockLLMProvider()
        with self.session_factory() as db:
            project = self._project(db)
            with patch("app.scripts.service.provider_registry.get", return_value=provider):
                task = create_script_generation_task(db, project.id)

            self.assertEqual(task.status, "queued")
            self.assertEqual(task.progress, 0)
            self.assertEqual(provider.phases, [])
            self.assertEqual(
                task.raw_response["checkpoint"]["pipeline_version"],
                SCRIPT_QUALITY_PIPELINE_VERSION,
            )
            with patch("app.scripts.service.provider_registry.get", return_value=provider):
                with self.assertRaises(HTTPException) as duplicate:
                    create_script_generation_task(db, project.id)
            self.assertEqual(duplicate.exception.status_code, 409)

    def test_worker_resumes_from_structured_draft_checkpoint(self):
        provider = RecordingMockLLMProvider()
        with self.session_factory() as db:
            project = self._project(db)
            with patch("app.scripts.service.provider_registry.get", return_value=provider):
                task = create_script_generation_task(db, project.id)
                task.raw_response = {
                    "checkpoint": {
                        "pipeline_version": SCRIPT_QUALITY_PIPELINE_VERSION,
                        "responses": {},
                        "pipeline_trace": {
                            "phases": [
                                {"phase": "story_blueprint", "status": "succeeded"},
                                {"phase": "script_draft", "status": "succeeded"},
                            ],
                            "fallbacks": [],
                        },
                        "blueprint": {
                            "premise": "Mia和Leo合作收拾积木。",
                            "required_phrases": [],
                            "beats": [{"beat_no": 1, "visible_event": "两人收拾积木。"}],
                        },
                        "draft": {
                            "content": "场景一：一起收拾",
                            "scenes": [
                                {
                                    "scene_no": 1,
                                    "title": "一起收拾",
                                    "location": "活动室",
                                    "time_of_day": "白天",
                                    "characters": ["Mia", "Leo"],
                                    "props": ["红色积木", "收纳盒"],
                                    "visible_action": "Mia和Leo把红色积木放进收纳盒。",
                                    "story_purpose": "完成合作",
                                    "start_state": "积木散落在地面。",
                                    "end_state": "积木已经收好。",
                                    "mood": "轻松",
                                    "source_evidence": "用户创意",
                                    "inferred_elements": [],
                                    "sound_cues": ["积木轻碰声"],
                                    "dialogues": [
                                        {
                                            "speaker": "Mia",
                                            "text": "Can you help me?",
                                            "translation_zh": "你能帮我吗？",
                                            "emotion": "友好",
                                            "source_type": "created",
                                            "sound_cues": [],
                                            "scene_no": 1,
                                        }
                                    ],
                                }
                            ],
                            "dialogues": [
                                {
                                    "speaker": "Mia",
                                    "text": "Can you help me?",
                                    "translation_zh": "你能帮我吗？",
                                    "emotion": "友好",
                                    "source_type": "created",
                                    "sound_cues": [],
                                    "scene_no": 1,
                                }
                            ],
                        },
                    }
                }
                db.add(task)
                db.commit()
                script, completed = execute_script_generation_task(db, task.id)

            self.assertEqual(completed.status, "succeeded")
            self.assertEqual(provider.phases, ["script_review"])
            self.assertEqual(script.scenes[0]["end_state"], "积木已经收好。")
            self.assertEqual(completed.result_payload["quality_gate"], "pass")
            self.assertNotIn("final_contract_review", completed.raw_response["checkpoint"])
            self.assertNotIn("revision_applied", completed.raw_response["checkpoint"])

    def test_restart_recovers_only_expired_lease_without_losing_checkpoint(self):
        provider = RecordingMockLLMProvider()
        with self.session_factory() as db:
            project = self._project(db)
            with patch("app.scripts.service.provider_registry.get", return_value=provider):
                task = create_script_generation_task(db, project.id)
            task.status = "running"
            task.lease_owner = "stopped-worker"
            task.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            task.progress = 56
            task.raw_response = {
                "checkpoint": {
                    "pipeline_version": SCRIPT_QUALITY_PIPELINE_VERSION,
                    "responses": {},
                    "pipeline_trace": {"phases": [], "fallbacks": []},
                    "blueprint": {"premise": "已保存的蓝图", "beats": []},
                }
            }
            db.add(task)
            db.commit()

            queued_ids = recover_script_generation_tasks(db)
            db.refresh(task)

            self.assertIn(task.id, queued_ids)
            self.assertEqual(task.status, "running")
            self.assertEqual(task.retry_count, 0)
            self.assertEqual(task.raw_response["checkpoint"]["blueprint"]["premise"], "已保存的蓝图")

    def test_worker_reuses_saved_patch_response_without_repeating_provider_call(self):
        provider = RecordingMockLLMProvider()
        draft_scene = {
            "scene_no": 1,
            "title": "一起收拾",
            "location": "活动室",
            "time_of_day": "白天",
            "characters": ["Mia", "Leo"],
            "props": ["红色积木"],
            "visible_action": "Mia和Leo把积木放进盒子。",
            "story_purpose": "完成合作",
            "start_state": "积木散落。",
            "end_state": "积木已经收好。",
            "mood": "轻松",
            "source_evidence": "用户创意",
            "inferred_elements": [],
            "sound_cues": [],
            "dialogues": [{"speaker": "Mia", "text": "Can you help me?", "scene_no": 1}],
        }
        replacement = {**draft_scene, "visible_action": "盒盖合上后，Mia和Leo相视微笑。"}
        review_issue = {
            "code": "PAYOFF_MISSING",
            "category": "causality",
            "severity": "must_fix",
            "scene_nos": [1],
            "problem": "缺少完成后的反应。",
            "evidence": "画面停在整理动作。",
            "repair_instruction": "补充两人的可见反应。",
            "protected_elements": ["请求对白"],
        }
        with self.session_factory() as db:
            project = self._project(db)
            with patch("app.scripts.service.provider_registry.get", return_value=provider):
                task = create_script_generation_task(db, project.id)
            task.raw_response = {
                "checkpoint": {
                    "pipeline_version": SCRIPT_QUALITY_PIPELINE_VERSION,
                    "responses": {
                        "script_patch": {
                            "status": "succeeded",
                            "provider_task_id": "saved-patch-response",
                            "provider": provider.name,
                            "model": provider.model,
                            "temperature": 0.35,
                            "raw_response": {
                                "text": json.dumps(
                                    {
                                        "script_patch": {
                                            "scene_replacements": [replacement],
                                            "resolved_issue_codes": ["PAYOFF_MISSING"],
                                            "unresolved_issue_codes": [],
                                            "preserved_elements": ["请求对白"],
                                        }
                                    },
                                    ensure_ascii=False,
                                )
                            },
                            "error_code": None,
                            "error_message": None,
                            "retryable": False,
                        }
                    },
                    "pipeline_trace": {
                        "phases": [
                            {"phase": "story_blueprint", "status": "succeeded"},
                            {"phase": "script_draft", "status": "succeeded"},
                            {"phase": "script_review", "status": "succeeded"},
                            {"phase": "script_patch", "status": "succeeded"},
                        ],
                        "fallbacks": [],
                    },
                    "blueprint": {"premise": "一起收拾积木", "required_phrases": [], "beats": []},
                    "draft": {
                        "content": "",
                        "scenes": [draft_scene],
                        "dialogues": draft_scene["dialogues"],
                    },
                    "review": {
                        "issues": [review_issue],
                    },
                    "review_available": True,
                }
            }
            db.add(task)
            db.commit()
            with patch("app.scripts.service.provider_registry.get", return_value=provider):
                script, completed = execute_script_generation_task(db, task.id)

            self.assertEqual(provider.phases, [])
            self.assertIn("相视微笑", script.scenes[0]["visible_action"])
            self.assertEqual(completed.result_payload["quality_gate"], "pass")
            self.assertEqual(completed.result_payload["patched_scene_nos"], [1])

    def test_not_submitted_script_phase_is_retried_once_from_last_checkpoint(self):
        provider = RetryOnceDraftProvider()
        with self.session_factory() as db:
            project = self._project(db)
            with patch("app.scripts.service.provider_registry.get", return_value=provider):
                task = create_script_generation_task(db, project.id)
                with self.assertRaises(TaskExecutionError) as raised:
                    execute_script_generation_task(db, task.id)
            self.assertEqual(
                raised.exception.submission_state,
                SubmissionState.NOT_SUBMITTED,
            )
            db.refresh(task)
            self.assertNotIn(
                "script_draft",
                task.raw_response["checkpoint"]["responses"],
            )

            runtime = LocalTaskRuntime(session_factory=self.session_factory)
            runtime._handle_execution_error(task.id, "test-worker", raised.exception)
            db.refresh(task)
            self.assertEqual(task.status, "queued")
            self.assertEqual(task.retry_count, 1)

            with patch("app.scripts.service.provider_registry.get", return_value=provider):
                _script, completed = execute_script_generation_task(db, task.id)

            self.assertEqual(completed.status, "succeeded")
            self.assertEqual(provider.phases.count("story_blueprint"), 1)
            self.assertEqual(provider.phases.count("script_draft"), 2)

    def test_inflight_script_submission_is_not_repeated_after_restart(self):
        provider = RecordingMockLLMProvider()
        with self.session_factory() as db:
            project = self._project(db)
            with patch("app.scripts.service.provider_registry.get", return_value=provider):
                task = create_script_generation_task(db, project.id)
            task.status = "running"
            task.raw_response = {
                "checkpoint": {
                    "pipeline_version": SCRIPT_QUALITY_PIPELINE_VERSION,
                    "responses": {},
                    "pipeline_trace": {"phases": [], "fallbacks": []},
                    "inflight_phase": {
                        "phase": "story_blueprint",
                        "attempted_at": datetime.now(timezone.utc).isoformat(),
                    },
                }
            }
            db.add(task)
            db.commit()

            with patch("app.scripts.service.provider_registry.get", return_value=provider):
                with self.assertRaises(ScriptPipelineFailure):
                    execute_script_generation_task(db, task.id)

            db.refresh(task)
            self.assertEqual(provider.phases, [])
            self.assertEqual(task.status, "failed")
            self.assertEqual(task.error_code, "provider_submission_uncertain")

    def test_reviewer_can_use_a_separate_provider_and_model(self):
        writer = RecordingMockLLMProvider()
        writer.name = "writer_mock"
        writer.model = "writer-model"
        reviewer = RecordingMockLLMProvider()
        reviewer.name = "reviewer_mock"
        reviewer.model = "reviewer-model"
        with self.session_factory() as db:
            project = self._project(db)
            db.add_all(
                [
                    AgentConfig(
                        agent_type="script_rewriter",
                        name="writer",
                        provider=writer.name,
                        model=writer.model,
                        settings={},
                        is_active=True,
                    ),
                    AgentConfig(
                        agent_type="script_reviewer",
                        name="reviewer",
                        provider=reviewer.name,
                        model=reviewer.model,
                        settings={},
                        is_active=True,
                    ),
                ]
            )
            db.commit()
            providers = {writer.name: writer, reviewer.name: reviewer}
            with patch(
                "app.scripts.service.provider_registry.get",
                side_effect=lambda _type, name: providers[name],
            ):
                task = create_script_generation_task(db, project.id)
                _script, completed = execute_script_generation_task(db, task.id)

            self.assertEqual(writer.phases, ["story_blueprint", "script_draft"])
            self.assertEqual(reviewer.phases, ["script_review"])
            self.assertEqual(completed.result_payload["review_provider"], reviewer.name)
            self.assertEqual(completed.result_payload["review_model"], reviewer.model)

    def test_review_failure_is_visible_without_discarding_valid_draft(self):
        provider = ReviewUnavailableProvider()
        with self.session_factory() as db:
            project = self._project(db)
            with patch("app.scripts.service.provider_registry.get", return_value=provider):
                task = create_script_generation_task(db, project.id)
                script, completed = execute_script_generation_task(db, task.id)

            self.assertEqual(script.status, "ready_for_review")
            self.assertEqual(completed.result_payload["quality_gate"], "review_unavailable")
            self.assertEqual(completed.raw_response["checkpoint"]["review"], {"issues": []})
            self.assertFalse(completed.raw_response["checkpoint"]["review_available"])
            self.assertFalse(completed.result_payload["patch_applied"])
            self.assertIn(
                "keep_valid_draft",
                {
                    item["action"]
                    for item in completed.raw_response["checkpoint"]["pipeline_trace"]["fallbacks"]
                },
            )

    def test_invalid_patch_is_rejected_and_marks_draft_for_attention(self):
        provider = InvalidPatchProvider()
        with self.session_factory() as db:
            project = self._project(db)
            with patch("app.scripts.service.provider_registry.get", return_value=provider):
                task = create_script_generation_task(db, project.id)
                script, completed = execute_script_generation_task(db, task.id)

            checkpoint = completed.raw_response["checkpoint"]
            self.assertEqual(script.scenes, checkpoint["draft"]["scenes"])
            self.assertEqual(completed.result_payload["quality_gate"], "needs_attention")
            self.assertEqual(completed.result_payload["unresolved_issue_codes"], ["PAYOFF_MISSING"])
            self.assertEqual(completed.result_payload["patched_scene_nos"], [])
            self.assertFalse(completed.result_payload["patch_applied"])
            self.assertIn("patch_error", checkpoint)

    def test_second_structure_parse_failure_fails_without_creating_script(self):
        provider = InvalidDraftProvider()
        with self.session_factory() as db:
            project = self._project(db)
            with patch("app.scripts.service.provider_registry.get", return_value=provider):
                task = create_script_generation_task(db, project.id)
                with self.assertRaises(ScriptPipelineFailure):
                    execute_script_generation_task(db, task.id)

            db.refresh(task)
            self.assertEqual(task.status, "failed")
            self.assertEqual(task.error_code, "script_generation_parse_failed")
            self.assertEqual(provider.phases, ["story_blueprint", "script_draft", "structure_recovery"])

            retry = TaskRepository().retry(
                db,
                task,
                allowed_task_types={"script_generation"},
            ).task
            self.assertNotIn(
                "structure_recovery",
                retry.raw_response["checkpoint"]["responses"],
            )
