import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db.session import create_database_engine, initialize_database
from app.models import Chapter, GenerationTask, Project, ProjectStageRun, Script
from app.providers.types import ProviderResponse, ProviderStatus
from app.services.entity_service import generate_entities, recover_entity_extraction_task


class _EntityProvider:
    name = "test_llm"
    model = "test-entity-model"

    def __init__(self, payload: dict):
        self.payload = payload

    def submit(self, _request):
        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id="provider-task-1",
            raw_response={"text": json.dumps(self.payload, ensure_ascii=False)},
        )


class EntityGenerationReviewFlowTests(unittest.TestCase):
    def test_entity_generation_records_asset_audit_without_stage_gate(self):
        provider = _EntityProvider(
            {
                "characters": [
                    {
                        "name": "Tom",
                        "role_type": "主角",
                        "age": "8岁",
                        "gender": "男",
                        "identity": "小学生",
                        "asset_spec": {
                            "state_variants": [
                                {
                                    "key": "helping_bird",
                                    "name": "帮助小鸟",
                                    "description": "Tom蹲下，双手轻轻托住小鸟。",
                                }
                            ]
                        },
                        "reference_prompts": {
                            "character_main_ref": {
                                "positive_prompt": "Tom，8岁小学生，明亮儿童二维动画角色设定图",
                                "negative_prompt": "",
                            },
                        },
                    }
                ],
                "scenes": [
                    {
                        "name": "树下",
                        "asset_spec": {},
                        "reference_prompt": {
                            "positive_prompt": "绿树下的安全活动空间，儿童二维动画",
                            "negative_prompt": "",
                        },
                    }
                ],
                "props": [
                    {
                        "name": "红色小球",
                        "asset_spec": {},
                        "reference_prompt": {
                            "positive_prompt": "红色小球单主体参考，儿童二维动画",
                            "negative_prompt": "",
                        },
                    }
                ],
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_database_engine(
                f"sqlite+pysqlite:///{(Path(temp_dir) / 'entities.sqlite3').as_posix()}"
            )
            initialize_database(engine)
            session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

            with session_factory() as db:
                project = Project(title="资产操作审计测试", style="明亮儿童二维动画")
                db.add(project)
                db.flush()
                chapter = Chapter(
                    project_id=project.id,
                    input_mode="imported_script",
                    outline="Tom helps a bird.",
                    source_text="Tom sees a bird under a tree and helps it.",
                    status="draft",
                )
                db.add(chapter)
                db.flush()
                script = Script(
                    project_id=project.id,
                    chapter_id=chapter.id,
                    content="Tom sees a bird under a tree.",
                    scenes=[
                        {
                            "scene_no": 1,
                            "title": "树下发现",
                            "location": "树下",
                            "characters": ["Tom"],
                            "props": ["红色小球"],
                            "visible_action": "Tom在树下蹲下。",
                            "source_evidence": "Tom sees a bird under a tree.",
                            "dialogues": [
                                {
                                    "speaker": "Tom",
                                    "text": "Are you okay?",
                                    "translation_zh": "你还好吗？",
                                    "emotion": "关心",
                                    "source_type": "adapted",
                                }
                            ],
                        }
                    ],
                    dialogues=[],
                    status="ready_for_review",
                )
                db.add(script)
                db.commit()

                with patch("app.services.entity_service.provider_registry.get", return_value=provider):
                    characters, scenes, props, task = generate_entities(db, project.id)

                self.assertEqual(task.status, "succeeded")
                self.assertEqual(len(characters), 1)
                self.assertEqual(len(scenes), 1)
                self.assertEqual(len(props), 1)
                self.assertTrue(
                    all(
                        item.status == "ready_for_review"
                        for item in [*characters, *scenes, *props]
                    )
                )
                self.assertEqual(
                    characters[0].asset_spec["state_variants"][0]["key"],
                    "helping_bird",
                )
                audit = db.scalar(
                    select(ProjectStageRun)
                    .where(ProjectStageRun.project_id == project.id)
                    .where(ProjectStageRun.stage == "assets")
                    .order_by(ProjectStageRun.created_at.desc())
                )
                self.assertIsNotNone(audit)
                self.assertEqual(audit.status, "succeeded")
                self.assertEqual(audit.output_payload["quality_blocker_count"], 0)

                failed_task = GenerationTask(
                    project_id=project.id,
                    task_type="entity_extraction",
                    provider="test_llm",
                    model="test-entity-model",
                    input_payload={
                        "script_id": script.id,
                        "script_content": script.content,
                        "script_scenes": script.scenes,
                        "previous_approved_entities": {},
                    },
                    raw_response={
                        "provider": {
                            "text": json.dumps(provider.payload, ensure_ascii=False),
                        }
                    },
                    status="failed",
                    progress=78,
                    error_code="entity_extraction_quality_failed",
                    error_message="旧任务保存了可恢复的 Provider 结果",
                )
                db.add(failed_task)
                db.commit()

                with patch("app.services.entity_service.provider_registry.get") as provider_get:
                    recovered_characters, recovered_scenes, recovered_props, recovered_task = (
                        recover_entity_extraction_task(db, failed_task.id)
                    )

                provider_get.assert_not_called()
                self.assertEqual(recovered_task.status, "succeeded")
                self.assertEqual(recovered_task.provider, "local_recovery")
                self.assertEqual(
                    recovered_task.input_payload["recovered_from_task_id"],
                    failed_task.id,
                )
                self.assertFalse(recovered_task.raw_response["recovery"]["provider_called"])
                self.assertTrue(
                    all(
                        item.status == "ready_for_review"
                        for item in [
                            *recovered_characters,
                            *recovered_scenes,
                            *recovered_props,
                        ]
                    )
                )
                recovery_audit = db.scalar(
                    select(ProjectStageRun)
                    .where(ProjectStageRun.project_id == project.id)
                    .where(ProjectStageRun.stage == "assets")
                    .order_by(ProjectStageRun.created_at.desc())
                )
                self.assertEqual(
                    recovery_audit.output_payload["recovery"]["source_task_id"],
                    failed_task.id,
                )

            engine.dispose()


if __name__ == "__main__":
    unittest.main()
