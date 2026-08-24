from pathlib import Path
import tempfile
import unittest

from sqlalchemy.orm import sessionmaker

from app.agents.image_generation import character_reference_prompt
from app.agents.prompt_engineering import build_shot_video_prompt
from app.agents.style import (
    DEFAULT_VISUAL_STYLE,
    DEFAULT_VISUAL_STYLE_DNA,
    image_style_dna,
)
from app.db.session import create_database_engine, initialize_database
from app.models import Character, Project, Shot
from app.schemas.project import CreativeSettings, ProjectCreate, ProjectUpdate
from app.services.project_service import create_project, update_project


LEGACY_STYLE = "明亮友好的二维动画，轮廓清楚，动作易懂"


class VisualStylePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(
            f"sqlite+pysqlite:///{(Path(self.temp_dir.name) / 'visual-style.sqlite3').as_posix()}"
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

    def test_default_is_visible_stylized_3d_but_explicit_2d_is_respected(self):
        self.assertEqual(image_style_dna(DEFAULT_VISUAL_STYLE), DEFAULT_VISUAL_STYLE_DNA)
        self.assertEqual(image_style_dna("动画"), DEFAULT_VISUAL_STYLE_DNA)
        self.assertEqual(image_style_dna("二维手绘动画"), "二维手绘动画")

        with self.session_factory() as db:
            project = create_project(
                db,
                ProjectCreate(
                    title="默认三维风格",
                    input_mode="ai_brief",
                    outline="两只小动物一起修好月亮灯。",
                ),
            )
            self.assertEqual(project.style, DEFAULT_VISUAL_STYLE)
            self.assertEqual(
                project.creative_settings["animation_style"],
                DEFAULT_VISUAL_STYLE,
            )

            explicit_2d = create_project(
                db,
                ProjectCreate(
                    title="用户明确二维",
                    input_mode="ai_brief",
                    outline="一只小猫寻找红色围巾。",
                    style="二维手绘动画",
                ),
            )
            self.assertEqual(explicit_2d.style, "二维手绘动画")
            self.assertEqual(
                explicit_2d.creative_settings["animation_style"],
                "二维手绘动画",
            )

            partial_update = update_project(
                db,
                explicit_2d,
                ProjectUpdate(creative_settings=CreativeSettings(english_level="A2")),
            )
            self.assertEqual(partial_update.style, "二维手绘动画")
            self.assertEqual(partial_update.creative_settings["english_level"], "A2")

    def test_style_update_rebases_subject_prompts_and_marks_video_prompt_stale(self):
        with self.session_factory() as db:
            project = Project(
                title="旧二维项目",
                style=LEGACY_STYLE,
                creative_settings={"english_level": "A1", "animation_style": LEGACY_STYLE},
            )
            db.add(project)
            db.flush()
            character = Character(
                project_id=project.id,
                name="Finn",
                fixed_prompt=f"{LEGACY_STYLE}风格。Finn是一只橙红色小狐狸；{LEGACY_STYLE}",
                asset_spec={
                    "image_prompts": {
                        "character_main_ref": {
                            "positive_prompt": f"{LEGACY_STYLE}风格。Finn是一只橙红色小狐狸；{LEGACY_STYLE}",
                            "negative_prompt": "字幕，水印，logo，可读文字",
                        }
                    }
                },
            )
            shot = Shot(
                project_id=project.id,
                shot_no=1,
                description="Finn抬头看月亮灯。",
                image_prompt=f"{LEGACY_STYLE}风格。Finn抬头看月亮灯；{LEGACY_STYLE}",
                video_prompt="任务：生成一段儿童二维动画英语短剧视频。",
                shot_card={
                    "schema_version": 3,
                    "image_prompts": {
                        "shot_storyboard": {
                            "positive_prompt": f"Finn抬头看月亮灯；{LEGACY_STYLE}",
                            "negative_prompt": "字幕，水印，logo，可读文字",
                        }
                    },
                },
            )
            db.add_all([character, shot])
            db.commit()

            updated = update_project(
                db,
                project,
                ProjectUpdate(style=DEFAULT_VISUAL_STYLE),
            )
            db.refresh(character)
            db.refresh(shot)

            self.assertEqual(updated.style, DEFAULT_VISUAL_STYLE)
            self.assertEqual(updated.creative_settings["animation_style"], DEFAULT_VISUAL_STYLE)
            self.assertNotIn("二维", character.fixed_prompt or "")
            self.assertNotIn(
                "二维",
                character.asset_spec["image_prompts"]["character_main_ref"]["positive_prompt"],
            )
            self.assertNotIn("二维", shot.image_prompt or "")
            self.assertTrue(shot.shot_card["prompt_stale"])
            self.assertIn("视觉风格", shot.shot_card["stale_reason"])
            self.assertIn("二维", shot.video_prompt or "")

            provider_prompt = character_reference_prompt(
                character,
                updated.style,
                "character_main_ref",
            )
            self.assertIn(DEFAULT_VISUAL_STYLE_DNA, provider_prompt)
            self.assertNotIn("二维", provider_prompt)

            video_prompt = build_shot_video_prompt(
                description="Finn抬头看月亮灯。",
                camera_shot="中景",
                camera_movement="固定机位",
                characters=[character],
                scene=None,
                animation_style=updated.style,
            )
            self.assertIn(f"视觉风格：{DEFAULT_VISUAL_STYLE}", video_prompt)
            self.assertNotIn(DEFAULT_VISUAL_STYLE_DNA, video_prompt)
            self.assertNotIn("二维", video_prompt)


if __name__ == "__main__":
    unittest.main()
