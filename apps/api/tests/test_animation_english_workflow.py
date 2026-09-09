from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.db.session import create_database_engine, initialize_database
from app.main import app
from app.models import (
    Asset,
    Chapter,
    Character,
    Dialogue,
    Project,
    ProjectStageRun,
    Prop,
    Scene,
    Script,
    Shot,
)
from app.providers.mock import MockLLMProvider, MockVideoProvider
from app.providers.types import ProviderResponse, ProviderStatus
from app.schemas.dialogue import DialogueInput, DialogueSaveRequest
from app.schemas.project import ProjectCreate
from app.services.dialogue_service import save_dialogues
from app.services.entity_service import generate_entities
from app.services.asset_resolver_service import resolve_generation_assets
from app.services.project_service import create_project
from app.scripts.service import generate_script_now
from app.production.shot_direction.service import generate_shots_now
from app.services.shot_service import (
    compile_shot_video_prompt,
    preview_shot_video_prompt,
    update_shot_reference_assets,
    update_shot_video_reference,
)


class RevisionMockLLMProvider(MockLLMProvider):
    name = "revision_mock"

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
                                        "code": "PAYOFF_REACTION_MISSING",
                                        "category": "causality",
                                        "severity": "must_fix",
                                        "scene_nos": [1],
                                        "problem": "盒盖合上后没有角色反应。",
                                        "evidence": "visible_action 只写到收拾地面。",
                                        "repair_instruction": "保留对白，只补充合作完成后的可见反应。",
                                        "protected_elements": ["Can you help me?", "Yes, I can!"],
                                    },
                                    {
                                        "code": "COLOR_OPTION",
                                        "category": "production",
                                        "severity": "editorial_note",
                                        "scene_nos": [1],
                                        "problem": "可选：让色调更活泼",
                                        "evidence": "",
                                        "repair_instruction": "仅供人工选择。",
                                        "protected_elements": [],
                                    },
                                ],
                            }
                        },
                        ensure_ascii=False,
                    )
                },
            )
        if phase == "script_patch":
            payload = json.loads(response.raw_response["text"])
            replacement = payload["script_patch"]["scene_replacements"][0]
            replacement["visible_action"] = (
                "Mia捡起红色积木，Leo把积木放进盒子；盒盖合上后，两人看着整洁的地面相视微笑。"
            )
            replacement["end_state"] = "积木全部收好，两人因合作成功而开心。"
            return ProviderResponse(
                status=ProviderStatus.SUCCEEDED,
                provider_task_id=response.provider_task_id,
                raw_response={"text": json.dumps(payload, ensure_ascii=False)},
            )
        return response


class AnimationEnglishWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(
            f"sqlite+pysqlite:///{(Path(self.temp_dir.name) / 'workflow.sqlite3').as_posix()}"
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

    def test_project_creation_supports_both_input_modes_and_frame_pairs(self):
        with self.session_factory() as db:
            ai_project = create_project(
                db,
                ProjectCreate(
                    title="红色小球",
                    input_mode="ai_brief",
                    outline="Mia and Leo learn to share a red ball.",
                ),
            )
            imported_project = create_project(
                db,
                ProjectCreate(
                    title="导入剧本",
                    input_mode="imported_script",
                    source_text="Mia says, “This is red.”",
                    aspect_ratio="9:16",
                    resolution="480x854",
                ),
            )

            self.assertEqual(ai_project.chapters[0].input_mode, "ai_brief")
            self.assertEqual(ai_project.creative_settings["english_level"], "A1")
            self.assertNotIn("audience_age", ai_project.creative_settings)
            self.assertNotIn("learning_objective", ai_project.creative_settings)
            self.assertEqual(ai_project.creative_settings["default_subtitle_mode"], "none")
            self.assertEqual(ai_project.resolution, "854x480")
            self.assertEqual(imported_project.chapters[0].input_mode, "imported_script")
            self.assertEqual(imported_project.chapters[0].source_text, "Mia says, “This is red.”")
            self.assertEqual(imported_project.aspect_ratio, "9:16")
            self.assertEqual(imported_project.resolution, "480x854")

        with self.assertRaises(ValidationError):
            ProjectCreate(title="缺少创意", input_mode="ai_brief", outline="")
        with self.assertRaises(ValidationError):
            ProjectCreate(
                title="错误画幅",
                input_mode="ai_brief",
                outline="A short lesson.",
                aspect_ratio="9:16",
                resolution="854x480",
            )

    def test_mock_chain_runs_without_stage_approval_gates(self):
        provider = MockLLMProvider()
        with self.session_factory() as db:
            project = create_project(
                db,
                ProjectCreate(
                    title="一起收拾玩具",
                    input_mode="ai_brief",
                    outline="Mia asks Leo to help put red blocks in a box.",
                ),
            )
            with (
                patch("app.scripts.service.provider_registry.get", return_value=provider),
                patch("app.services.entity_service.provider_registry.get", return_value=provider),
                patch("app.production.shot_direction.service.provider_registry.get", return_value=provider),
            ):
                script, script_task = generate_script_now(db, project.id)
                with self.assertRaises(HTTPException) as missing_entities:
                    generate_shots_now(db, project.id)
                self.assertEqual(missing_entities.exception.status_code, 409)
                self.assertEqual(
                    missing_entities.exception.detail,
                    "生成片段方案前，请先从剧本提取角色和场景设定",
                )
                characters, scenes, props, entity_task = generate_entities(db, project.id)
                shots, shot_task = generate_shots_now(db, project.id)

            self.assertEqual(script_task.status, "succeeded")
            self.assertEqual(
                [
                    item["phase"]
                    for item in script_task.raw_response["checkpoint"]["pipeline_trace"]["phases"]
                ],
                ["story_blueprint", "script_draft", "script_review"],
            )
            self.assertEqual(script_task.result_payload["quality_gate"], "pass")
            self.assertFalse(script_task.result_payload["patch_applied"])
            self.assertEqual(script_task.result_payload["unresolved_issue_codes"], [])
            self.assertTrue(
                {
                    "review_verdict",
                    "must_fix_count",
                    "unresolved_must_fix_count",
                    "revision_applied",
                }.isdisjoint(script_task.result_payload)
            )
            self.assertNotIn("final_contract_review", script_task.raw_response["checkpoint"])
            self.assertNotIn("revision_applied", script_task.raw_response["checkpoint"])
            self.assertEqual(entity_task.status, "succeeded")
            self.assertEqual(shot_task.status, "succeeded")
            self.assertEqual(script.status, "ready_for_review")
            self.assertTrue(characters)
            self.assertTrue(scenes)
            self.assertTrue(props)
            self.assertEqual(len(shots), 8)
            self.assertEqual(sum(float(shot.duration_sec or 0) for shot in shots), 90)
            self.assertTrue(all(shot.duration_sec is not None for shot in shots))
            self.assertEqual(
                shots[0].shot_card["segment_plan"]["duration_mode"],
                "fixed",
            )
            self.assertEqual(
                float(shots[0].shot_card["segment_plan"]["planned_duration_sec"]),
                float(shots[0].duration_sec),
            )
            self.assertTrue(
                all("duration_sec" not in beat for beat in shots[0].shot_card["beats"])
            )
            self.assertIn("Can you help me?", shots[0].video_prompt)
            self.assertIn("积木轻轻碰撞", shots[0].video_prompt)
            self.assertIn("不得出现字幕", shots[0].video_prompt)
            self.assertIn("镜头1：", shots[0].video_prompt)
            self.assertNotIn("第1秒", shots[0].video_prompt)
            self.assertIn("镜头3：", shots[0].video_prompt)
            self.assertNotIn("最后，", shots[0].video_prompt)
            self.assertNotIn("秒（持续", shots[0].video_prompt)
            self.assertIn("切镜有动机", shots[0].video_prompt)
            self.assertEqual(
                shots[0].shot_card["segment_plan"]["internal_shot_count"],
                3,
            )
            self.assertEqual(
                {
                    run.stage
                    for run in db.scalars(
                        select(ProjectStageRun).where(
                            ProjectStageRun.project_id == project.id
                        )
                    ).all()
                },
                {"script", "assets", "production"},
            )

    def test_script_pipeline_applies_one_targeted_revision_for_must_fix(self):
        provider = RevisionMockLLMProvider()
        with self.session_factory() as db:
            project = create_project(
                db,
                ProjectCreate(
                    title="一起收拾玩具",
                    input_mode="ai_brief",
                    outline="Mia asks Leo to help put red blocks in a box.",
                ),
            )
            with patch("app.scripts.service.provider_registry.get", return_value=provider):
                script, task = generate_script_now(db, project.id)

            self.assertEqual(
                [
                    item["phase"]
                    for item in task.raw_response["checkpoint"]["pipeline_trace"]["phases"]
                ],
                ["story_blueprint", "script_draft", "script_review", "script_patch"],
            )
            self.assertTrue(task.result_payload["patch_applied"])
            self.assertEqual(task.result_payload["issue_counts"]["must_fix"], 1)
            self.assertEqual(task.result_payload["issue_counts"]["editorial_note"], 1)
            self.assertEqual(task.result_payload["unresolved_issue_codes"], [])
            self.assertEqual(task.result_payload["quality_gate"], "pass")
            self.assertEqual(task.result_payload["patched_scene_nos"], [1])
            self.assertEqual(
                task.result_payload["resolved_issue_codes"],
                ["PAYOFF_REACTION_MISSING"],
            )
            self.assertNotIn("script_revision", task.input_payload["temperatures"])
            self.assertIn("相视微笑", script.scenes[0]["visible_action"])
            self.assertNotIn(
                "相视微笑",
                task.raw_response["checkpoint"]["draft"]["scenes"][0]["visible_action"],
            )
            self.assertEqual(
                [
                    issue["problem"]
                    for issue in task.raw_response["checkpoint"]["review"]["issues"]
                    if issue["severity"] == "editorial_note"
                ],
                ["可选：让色调更活泼"],
            )

    def test_prompt_stays_editable_until_explicit_recompile(self):
        with self.session_factory() as db:
            project = Project(
                title="颜色与分享",
                style="明亮儿童二维动画",
                creative_settings={
                    "english_level": "A1",
                    "animation_style": "明亮儿童二维动画",
                    "dialogue_language": "en",
                    "translation_language": "zh-CN",
                    "default_subtitle_mode": "none",
                },
            )
            db.add(project)
            db.flush()
            chapter = Chapter(
                project_id=project.id,
                input_mode="ai_brief",
                outline="Mia shares a red ball.",
            )
            db.add(chapter)
            db.flush()
            script = Script(
                project_id=project.id,
                chapter_id=chapter.id,
                version=1,
                content="Mia shares a red ball.",
                scenes=[],
                dialogues=[],
            )
            db.add(script)
            character = Character(
                project_id=project.id,
                name="Mia",
                fixed_prompt="Mia wears a yellow shirt.",
                asset_spec={
                    "state_variants": [
                        {
                            "key": "holding-ball",
                            "name": "拿球",
                            "description": "Mia双手拿着红色小球。",
                            "scene_nos": [1],
                        }
                    ]
                },
            )
            scene = Scene(
                project_id=project.id,
                name="活动室",
                asset_spec={"state_variants": []},
            )
            prop = Prop(
                project_id=project.id,
                name="红色积木",
                asset_spec={"state_variants": []},
            )
            db.add_all([character, scene, prop])
            db.flush()
            dialogue = Dialogue(
                project_id=project.id,
                script_id=script.id,
                character_id=character.id,
                speaker_name="Mia",
                text="Look! A red ball!",
                translation_zh="看！一个红色的球！",
                emotion="开心",
                sequence_order=0,
                beat_id="beat-1",
                sound_cues=["小球滚动声"],
            )
            db.add(dialogue)
            db.flush()
            shot = Shot(
                project_id=project.id,
                script_id=script.id,
                scene_id=scene.id,
                shot_no=1,
                description="Mia举起红色小球。",
                duration_sec=Decimal("3"),
                dialogue_ids=[dialogue.id],
                character_ids=[character.id],
                prop_ids=[prop.id],
                shot_card={
                    "schema_version": 2,
                    "source_scene_no": 1,
                    "beats": [
                        {
                            "beat_id": "beat-1",
                            "duration_sec": 3,
                            "camera": "中景平视，固定机位",
                            "action": "Mia举起红色小球。",
                            "dialogue_ids": [dialogue.id],
                            "sound_cues": ["小球滚动声"],
                        }
                    ],
                    "prompt_stale": True,
                },
                video_prompt="USER EDITED PROMPT",
            )
            db.add(shot)
            db.flush()
            dialogue.shot_id = shot.id
            db.add(dialogue)
            db.commit()

            preview = preview_shot_video_prompt(db, shot.id)
            self.assertTrue(preview["stale"])
            self.assertNotIn("@角色_", preview["prompt"])
            self.assertNotIn("@场景_", preview["prompt"])
            self.assertNotIn("@道具_", preview["prompt"])
            self.assertIn("{Look! A red ball!}", preview["prompt"])
            self.assertIn("Mia（开心）说", preview["prompt"])
            self.assertIn("<小球滚动声>", preview["prompt"])
            self.assertIn("不得出现字幕", preview["prompt"])
            self.assertEqual(shot.video_prompt, "USER EDITED PROMPT")

            compiled = compile_shot_video_prompt(db, shot.id)
            first_compiled_prompt = compiled.video_prompt
            self.assertEqual(first_compiled_prompt, preview["prompt"])
            self.assertFalse(preview_shot_video_prompt(db, shot.id)["stale"])

            save_dialogues(
                db,
                project.id,
                DialogueSaveRequest(
                    dialogues=[
                        DialogueInput(
                            id=dialogue.id,
                            shot_id=shot.id,
                            character_id=character.id,
                            speaker_name="Mia",
                            text="Can I have the red ball, please?",
                            translation_zh=None,
                            emotion="礼貌地",
                            sequence_order=0,
                            beat_id="beat-1",
                            sound_cues=["轻快脚步声"],
                        )
                    ]
                ),
            )
            db.refresh(shot)
            self.assertTrue(shot.shot_card["prompt_stale"])
            self.assertEqual(shot.video_prompt, first_compiled_prompt)
            changed_preview = preview_shot_video_prompt(db, shot.id)
            self.assertTrue(changed_preview["stale"])
            self.assertIn("Can I have the red ball, please?", changed_preview["prompt"])
            self.assertNotIn("Look! A red ball!", changed_preview["prompt"])

            recompiled = compile_shot_video_prompt(db, shot.id)
            self.assertEqual(recompiled.video_prompt, changed_preview["prompt"])
            self.assertFalse(preview_shot_video_prompt(db, shot.id)["stale"])

    def test_openapi_removes_gate_and_tts_contracts_and_exposes_video_capabilities(self):
        schema = app.openapi()
        paths = schema["paths"]
        serialized = str(schema)

        self.assertFalse(any("/workflow/gates" in path for path in paths))
        self.assertFalse(any("/tts" in path or "/audio" in path for path in paths))
        self.assertNotIn("voice_profile", serialized)
        self.assertNotIn("audio_asset_id", serialized)
        self.assertIn("/api/projects/{project_id}/dialogues", paths)
        self.assertIn("/api/shots/{shot_id}/video-prompt-preview", paths)
        self.assertIn("/api/shots/{shot_id}/compile-video-prompt", paths)
        self.assertIn("/api/shots/{shot_id}/reference-assets", paths)
        self.assertIn("/api/assets/{asset_id}/extract-frame", paths)

        descriptor = MockVideoProvider().descriptor()
        self.assertTrue(descriptor["native_audio"])
        self.assertTrue(descriptor["reference_images"])
        self.assertTrue(descriptor["multi_reference"])
        self.assertEqual(descriptor["max_duration_sec"], 30)
        self.assertEqual(
            descriptor["supported_resolutions"],
            ["854x480", "480x854", "1280x720", "720x1280"],
        )

    def test_explicit_shot_reference_assets_preserve_exact_variants(self):
        with self.session_factory() as db:
            project = Project(title="显式引用", style="儿童动画")
            other_project = Project(title="其他项目", style="儿童动画")
            db.add_all([project, other_project])
            db.flush()
            character = Character(project_id=project.id, name="Mia")
            scene = Scene(project_id=project.id, name="教室")
            other_scene = Scene(project_id=project.id, name="操场")
            prop = Prop(project_id=project.id, name="红球")
            db.add_all([character, scene, other_scene, prop])
            db.flush()
            shot = Shot(
                project_id=project.id,
                shot_no=1,
                description="Mia在教室里举起红球。",
                character_ids=[],
                prop_ids=[],
                shot_card={"beats": []},
            )
            legacy_shot = Shot(
                project_id=project.id,
                shot_no=2,
                description="Mia在教室里微笑。",
                scene_id=scene.id,
                character_ids=[character.id],
                prop_ids=[],
                shot_card={"beats": []},
            )
            db.add_all([shot, legacy_shot])
            db.flush()

            def adopted_asset(entity_type, entity_id, role, variant_key, *, owner_id=None):
                return Asset(
                    project_id=owner_id or project.id,
                    asset_type="image",
                    asset_role=role,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    variant_key=variant_key,
                    version=1,
                    uri=f"mock://images/{entity_type}-{variant_key}.png",
                    status="approved",
                    is_selected=True,
                )

            character_asset = adopted_asset(
                "character",
                character.id,
                "character_main_ref",
                "holding-ball",
            )
            base_character_asset = adopted_asset(
                "character",
                character.id,
                "character_main_ref",
                "base",
            )
            scene_asset = adopted_asset("scene", scene.id, "scene_ref", "sunny")
            other_scene_asset = adopted_asset("scene", other_scene.id, "scene_ref", "base")
            prop_asset = adopted_asset("prop", prop.id, "prop_ref", "clean")
            storyboard_asset = adopted_asset(
                "shot",
                shot.id,
                "shot_storyboard",
                "base",
            )
            foreign_asset = adopted_asset(
                "character",
                character.id,
                "character_main_ref",
                "base",
                owner_id=other_project.id,
            )
            historical_character_asset = adopted_asset(
                "character",
                character.id,
                "character_main_ref",
                "reading",
            )
            historical_character_asset.version = 2
            historical_character_asset.is_selected = False
            unavailable_character_asset = adopted_asset(
                "character",
                character.id,
                "character_main_ref",
                "draft",
            )
            unavailable_character_asset.version = 3
            unavailable_character_asset.status = "ready_for_review"
            unavailable_character_asset.is_selected = False
            db.add_all(
                [
                    character_asset,
                    base_character_asset,
                    scene_asset,
                    other_scene_asset,
                    prop_asset,
                    storyboard_asset,
                    foreign_asset,
                    historical_character_asset,
                    unavailable_character_asset,
                ]
            )
            db.commit()

            updated = update_shot_reference_assets(
                db,
                shot.id,
                [scene_asset.id, character_asset.id, prop_asset.id],
            )
            self.assertEqual(
                updated.shot_card["reference_asset_ids"],
                [scene_asset.id, character_asset.id, prop_asset.id],
            )
            self.assertEqual(updated.scene_id, scene.id)
            self.assertEqual(updated.character_ids, [character.id])
            self.assertEqual(updated.prop_ids, [prop.id])
            self.assertTrue(updated.shot_card["prompt_stale"])

            preview = preview_shot_video_prompt(db, shot.id)
            self.assertNotIn("当前片段首帧与构图起点", preview["prompt"])
            self.assertIn("图片1=场景“教室”", preview["prompt"])
            self.assertIn("图片2=角色“Mia”", preview["prompt"])
            self.assertIn("Mia在教室里举起红球", preview["prompt"])
            self.assertNotIn("独立道具参考图", preview["prompt"])
            self.assertNotIn("@角色_", preview["prompt"])
            self.assertNotIn("@场景_", preview["prompt"])
            self.assertNotIn("@道具_", preview["prompt"])
            self.assertEqual(
                [item["asset_id"] for item in preview["reference_assets"]],
                [scene_asset.id, character_asset.id],
            )

            update_shot_video_reference(db, shot.id, storyboard_asset.id)
            first_frame_preview = preview_shot_video_prompt(db, shot.id)
            self.assertIn("图片1=当前片段首帧", first_frame_preview["prompt"])
            self.assertIn("图片2=场景“教室”", first_frame_preview["prompt"])
            self.assertIn("图片3=角色“Mia”", first_frame_preview["prompt"])
            self.assertEqual(
                [item["asset_id"] for item in first_frame_preview["reference_assets"]],
                [storyboard_asset.id, scene_asset.id, character_asset.id],
            )

            update_shot_video_reference(db, shot.id, None)
            no_first_frame_preview = preview_shot_video_prompt(db, shot.id)
            self.assertNotIn("当前片段首帧与构图起点", no_first_frame_preview["prompt"])
            resolution = resolve_generation_assets(
                db,
                project.id,
                stage="shot_video",
                shot=updated,
            )
            self.assertEqual(
                [
                    item.asset_id
                    for item in resolution.reference_assets
                    if item.entity_type in {"character", "scene", "prop"}
                ],
                [scene_asset.id, character_asset.id],
            )
            self.assertIn(
                prop_asset.id,
                resolution.reference_metadata["omitted_video_reference_asset_ids"],
            )
            self.assertEqual(
                resolution.reference_metadata["reference_binding_source"],
                "explicit",
            )

            with self.assertRaises(HTTPException):
                update_shot_reference_assets(
                    db,
                    shot.id,
                    [scene_asset.id, other_scene_asset.id],
                )
            with self.assertRaises(HTTPException):
                update_shot_reference_assets(db, shot.id, [foreign_asset.id])
            with self.assertRaises(HTTPException):
                update_shot_reference_assets(
                    db,
                    shot.id,
                    [scene_asset.id, unavailable_character_asset.id],
                )

            historical = update_shot_reference_assets(
                db,
                shot.id,
                [scene_asset.id, historical_character_asset.id, prop_asset.id],
            )
            self.assertEqual(
                historical.shot_card["reference_asset_ids"],
                [scene_asset.id, historical_character_asset.id, prop_asset.id],
            )
            historical_preview = preview_shot_video_prompt(db, shot.id)
            self.assertIn("图片2=角色“Mia”", historical_preview["prompt"])
            self.assertEqual(
                historical_preview["reference_assets"][1]["asset_id"],
                historical_character_asset.id,
            )

            cleared = update_shot_reference_assets(db, shot.id, [])
            self.assertEqual(cleared.shot_card["reference_asset_ids"], [])
            self.assertIsNone(cleared.scene_id)
            self.assertEqual(cleared.character_ids, [])
            self.assertEqual(cleared.prop_ids, [])
            cleared_resolution = resolve_generation_assets(
                db,
                project.id,
                stage="shot_video",
                shot=cleared,
            )
            self.assertEqual(
                [
                    item.asset_id
                    for item in cleared_resolution.reference_assets
                    if item.entity_type in {"character", "scene", "prop"}
                ],
                [],
            )

            legacy_resolution = resolve_generation_assets(
                db,
                project.id,
                stage="shot_video",
                shot=legacy_shot,
            )
            self.assertEqual(
                legacy_resolution.reference_metadata["reference_binding_source"],
                "automatic",
            )
            self.assertIn(
                base_character_asset.id,
                [item.asset_id for item in legacy_resolution.reference_assets],
            )


class LocalSQLiteMigrationTests(unittest.TestCase):
    def test_destructive_legacy_columns_are_backed_up_and_rows_remain_readable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "legacy.sqlite3"
            engine = create_database_engine(
                f"sqlite+pysqlite:///{database_path.as_posix()}"
            )
            initialize_database(engine)
            session_factory = sessionmaker(
                bind=engine,
                autoflush=False,
                autocommit=False,
            )
            with session_factory() as db:
                project = Project(title="旧项目", style="儿童二维动画")
                db.add(project)
                db.flush()
                chapter = Chapter(
                    project_id=project.id,
                    input_mode="imported_script",
                    source_text="Mia says hello.",
                )
                db.add(chapter)
                db.flush()
                script = Script(
                    project_id=project.id,
                    chapter_id=chapter.id,
                    content="Mia: Hello!",
                    scenes=[],
                    dialogues=[],
                )
                character = Character(project_id=project.id, name="Mia")
                db.add_all([script, character])
                db.flush()
                dialogue = Dialogue(
                    project_id=project.id,
                    script_id=script.id,
                    speaker_name="Mia",
                    text="Hello!",
                    sequence_order=0,
                )
                asset = Asset(
                    project_id=project.id,
                    asset_type="video",
                    asset_role="shot_video",
                    entity_type="project",
                    entity_id=project.id,
                    version=1,
                    uri="mock://videos/legacy.mp4",
                    status="approved",
                    is_selected=True,
                )
                db.add_all([dialogue, asset])
                db.commit()
                ids = {
                    "project": project.id,
                    "script": script.id,
                    "character": character.id,
                    "dialogue": dialogue.id,
                    "asset": asset.id,
                }

            with engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM local_schema_migrations "
                        "WHERE version = '20260730_animation_english_workflow_v1'"
                    )
                )
                connection.execute(
                    text("ALTER TABLE characters ADD COLUMN voice_profile JSON")
                )
                connection.execute(
                    text("ALTER TABLE dialogues ADD COLUMN audio_asset_id VARCHAR(36)")
                )
                connection.execute(
                    text(
                        "CREATE TABLE project_stages ("
                        "id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36), stage VARCHAR(80))"
                    )
                )

            initialize_database(engine)

            inspector = inspect(engine)
            self.assertNotIn(
                "voice_profile",
                {column["name"] for column in inspector.get_columns("characters")},
            )
            self.assertNotIn(
                "audio_asset_id",
                {column["name"] for column in inspector.get_columns("dialogues")},
            )
            self.assertNotIn("project_stages", inspector.get_table_names())
            self.assertTrue(
                list(
                    database_path.parent.glob(
                        "legacy.sqlite3.pre-20260730_animation_english_workflow_v1-*.bak"
                    )
                )
            )
            with session_factory() as db:
                self.assertEqual(db.get(Project, ids["project"]).title, "旧项目")
                self.assertEqual(db.get(Script, ids["script"]).content, "Mia: Hello!")
                self.assertEqual(db.get(Character, ids["character"]).name, "Mia")
                self.assertEqual(db.get(Dialogue, ids["dialogue"]).text, "Hello!")
                self.assertEqual(
                    db.get(Asset, ids["asset"]).uri,
                    "mock://videos/legacy.mp4",
                )
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
