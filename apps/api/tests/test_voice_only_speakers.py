import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.agents.script_screenplay import parse_screenplay_response
from app.db.session import create_database_engine, initialize_database
from app.models import Chapter, Character, GenerationTask, Project, Script
from app.providers.types import ProviderResponse, ProviderStatus
from app.services.dialogue_service import list_dialogues
from app.services.entity_service import generate_entities, get_current_entities
from app.services.image_generation_service import generate_single_reference_image_candidate


class _EntityProvider:
    name = "test_llm"
    model = "test-entity-model"

    def __init__(self, payload: dict):
        self.payload = payload

    def submit(self, _request):
        return ProviderResponse(
            status=ProviderStatus.SUCCEEDED,
            provider_task_id="voice-only-provider-task",
            raw_response={"text": json.dumps(self.payload, ensure_ascii=False)},
        )


class VoiceOnlySpeakerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(
            f"sqlite+pysqlite:///{(Path(self.temp_dir.name) / 'voice-only.sqlite3').as_posix()}"
        )
        initialize_database(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_screenplay_keeps_voice_dialogue_but_removes_voice_from_visual_cast(self):
        payload = {
            "scenes": [
                {
                    "scene_no": 1,
                    "title": "冰箱亮起",
                    "characters": ["Lucas", "AI 引导员", "旁白"],
                    "visible_action": "Lucas站在冰箱前，冰箱轮廓轻轻亮起。",
                    "dialogues": [
                        {"speaker": "AI 引导员", "text": "Let us help him!"},
                        {"speaker": "旁白", "text": "This is a fridge."},
                    ],
                }
            ]
        }

        result = parse_screenplay_response(json.dumps(payload, ensure_ascii=False))

        self.assertEqual(result["scenes"][0]["characters"], ["Lucas"])
        self.assertEqual(
            [item["speaker"] for item in result["dialogues"]],
            ["AI 引导员", "旁白"],
        )
        self.assertIn("AI 引导员：Let us help him!", result["content"])
        self.assertNotIn("出场：Lucas、AI 引导员", result["content"])

    def test_visibly_embodied_guide_remains_a_character(self):
        payload = {
            "scenes": [
                {
                    "scene_no": 1,
                    "title": "引导员出现",
                    "characters": ["AI 引导员"],
                    "visible_action": "AI 引导员的圆形机器人形象走到屏幕中央并挥手。",
                    "dialogues": [
                        {"speaker": "AI 引导员", "text": "Welcome, everyone!"},
                    ],
                }
            ]
        }

        result = parse_screenplay_response(json.dumps(payload, ensure_ascii=False))

        self.assertEqual(result["scenes"][0]["characters"], ["AI 引导员"])

    def test_entity_generation_drops_voice_only_speaker_and_leaves_dialogue_unbound(self):
        provider = _EntityProvider(
            {
                "characters": [
                    {"name": "Lucas", "identity": "饥饿的小男孩", "asset_spec": {}},
                    {"name": "AI 引导员", "identity": "温柔的虚拟声音", "asset_spec": {}},
                ],
                "scenes": [{"name": "温馨卡通厨房", "asset_spec": {}}],
                "props": [],
            }
        )
        with self.session_factory() as db:
            project, script = self._project_with_voice_only_script(db)

            with patch("app.services.entity_service.provider_registry.get", return_value=provider):
                characters, _scenes, _props, task = generate_entities(db, project.id)

            self.assertEqual([item.name for item in characters], ["Lucas"])
            self.assertEqual(task.result_payload["character_count"], 1)
            self.assertEqual(
                [item["name"] for item in task.raw_response["agent_result"]["characters"]],
                ["Lucas"],
            )
            dialogues = list_dialogues(db, project.id)
            self.assertEqual(len(dialogues), 1)
            self.assertEqual(dialogues[0].speaker_name, "AI 引导员")
            self.assertIsNone(dialogues[0].character_id)
            db.refresh(script)
            self.assertEqual(script.scenes[0]["characters"], ["Lucas"])

    def test_legacy_voice_only_character_cannot_create_an_image_task(self):
        with self.session_factory() as db:
            project, _script = self._project_with_voice_only_script(db)
            narrator = Character(
                project_id=project.id,
                name="AI 引导员",
                identity="只在画外发声的虚拟引导员",
                asset_spec={"reference_plan": {"required": True}},
                status="approved",
            )
            db.add(narrator)
            db.commit()

            characters, _scenes, _props = get_current_entities(db, project.id)
            self.assertEqual(characters, [])

            with self.assertRaises(HTTPException) as raised:
                generate_single_reference_image_candidate(
                    db,
                    project.id,
                    entity_type="character",
                    entity_id=narrator.id,
                    asset_role="character_main_ref",
                )

            self.assertEqual(raised.exception.status_code, 404)
            self.assertEqual(list(db.scalars(select(GenerationTask)).all()), [])

    @staticmethod
    def _project_with_voice_only_script(db):
        project = Project(title="冰箱", style="明亮儿童动画")
        db.add(project)
        db.flush()
        chapter = Chapter(
            project_id=project.id,
            input_mode="imported_script",
            outline="Lucas needs help opening the fridge.",
            source_text="An AI guide speaks off-screen while Lucas waits.",
            status="draft",
        )
        db.add(chapter)
        db.flush()
        script = Script(
            project_id=project.id,
            chapter_id=chapter.id,
            content="AI 引导员在画外邀请孩子帮助Lucas。",
            scenes=[
                {
                    "scene_no": 1,
                    "title": "冰箱亮起",
                    "location": "温馨卡通厨房",
                    "characters": ["Lucas", "AI 引导员"],
                    "props": ["冰箱"],
                    "visible_action": "Lucas站在冰箱前，冰箱轮廓轻轻亮起。",
                    "dialogues": [
                        {
                            "speaker": "AI 引导员",
                            "text": "Let us help him!",
                            "translation_zh": "我们来帮帮他！",
                        }
                    ],
                }
            ],
            dialogues=[],
            status="ready_for_review",
        )
        db.add(script)
        db.commit()
        return project, script


if __name__ == "__main__":
    unittest.main()
