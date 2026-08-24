import unittest
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete

from app.db import SessionLocal
from app.db.health import check_database
from app.models import Asset, Character, Project, Scene, Shot
from app.services.patch_pipeline_service import AVD_PATCH_PIPELINE_VERSION, analyze_patch_impact


def _database_available() -> bool:
    try:
        return check_database()
    except Exception:
        return False


@unittest.skipUnless(_database_available(), "database is not available")
class PatchPipelineIntegrationTests(unittest.TestCase):
    def test_character_patch_finds_related_shots_and_assets(self):
        title = f"__codex_patch_impact_{uuid4().hex}__"
        with SessionLocal() as db:
            db.execute(delete(Project).where(Project.title == title))
            db.commit()

            project = Project(title=title, style="赛博悬疑", target_duration_sec=90)
            db.add(project)
            db.flush()

            character = Character(
                project_id=project.id,
                name="沈照",
                fixed_prompt="沈照：眉心细疤，深靛蓝武服",
                status="approved",
            )
            scene = Scene(
                project_id=project.id,
                name="雨夜便利店",
                fixed_prompt="雨夜便利店，冷白灯，湿地反光",
                status="approved",
            )
            db.add_all([character, scene])
            db.flush()

            shot = Shot(
                project_id=project.id,
                scene_id=scene.id,
                shot_no=1,
                is_current=True,
                description="沈照在柜台前发现异常提示灯",
                camera_shot="中景",
                camera_movement="缓慢前推",
                duration_sec=Decimal("3.0"),
                character_ids=[character.id],
                prop_ids=[],
                shot_card={"frame_plan": {"first_frame": "柜台冷白灯"}},
                image_prompt="clean shot image prompt",
                video_prompt="clean shot video prompt",
                negative_prompt="no text, no watermark",
                status="approved",
            )
            db.add(shot)
            db.flush()

            character_asset = Asset(
                project_id=project.id,
                asset_type="image",
                asset_role="character_main_ref",
                entity_type="character",
                entity_id=character.id,
                version=1,
                uri="mock://character.png",
                mime_type="image/png",
                width=1280,
                height=720,
                status="approved",
                is_selected=True,
            )
            shot_asset = Asset(
                project_id=project.id,
                asset_type="image",
                asset_role="shot_storyboard",
                entity_type="shot",
                entity_id=shot.id,
                version=1,
                uri="mock://shot.png",
                mime_type="image/png",
                width=1280,
                height=720,
                status="approved",
                is_selected=True,
            )
            db.add_all([character_asset, shot_asset])
            db.commit()

            report = analyze_patch_impact(
                db,
                project.id,
                target_type="character",
                target_id=character.id,
                field="fixed_prompt",
                change_summary="给沈照增加左脸疤痕",
            )

            self.assertEqual(report["engine"], AVD_PATCH_PIPELINE_VERSION)
            self.assertEqual([item["id"] for item in report["affected_shots"]], [shot.id])
            self.assertEqual(report["affected_shots"][0]["reason"], "镜头绑定该角色")
            self.assertEqual(report["affected_stages"], ["assets", "production", "export"])
            affected_asset_ids = {item["id"] for item in report["affected_assets"]}
            self.assertIn(character_asset.id, affected_asset_ids)
            self.assertIn(shot_asset.id, affected_asset_ids)
            self.assertTrue(report["requires_human_review"])
            self.assertIn("只重生成受影响镜头的视频候选", report["recommended_actions"])

            db.execute(delete(Project).where(Project.id == project.id))
            db.commit()


if __name__ == "__main__":
    unittest.main()
