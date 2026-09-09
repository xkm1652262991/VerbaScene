import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.session import create_database_engine, initialize_database
from app.models import Asset, Character, Project, Shot
from app.services.asset_lifecycle_service import delete_asset, select_asset, upload_image_asset
from app.services.asset_repository import list_assets_page, list_current_assets


class AssetLifecycleServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(
            f"sqlite+pysqlite:///{(Path(self.temp_dir.name) / 'assets.sqlite3').as_posix()}"
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

    def test_upload_query_select_and_delete_preserve_version_lifecycle(self):
        storage_root = Path(self.temp_dir.name) / "storage"
        with (
            patch.object(settings, "storage_root", str(storage_root)),
            patch.object(settings, "public_storage_base_url", "http://api.test/storage"),
            self.session_factory() as db,
        ):
            project = Project(title="资产生命周期")
            db.add(project)
            db.flush()
            character = Character(project_id=project.id, name="Mia", status="approved")
            db.add(character)
            db.commit()

            first = upload_image_asset(
                db,
                project.id,
                content=b"first-image",
                filename="mia.png",
                content_type="image/png",
                entity_type="character",
                entity_id=character.id,
            )
            second = upload_image_asset(
                db,
                project.id,
                content=b"second-image",
                filename="mia.png",
                content_type="image/png",
                entity_type="character",
                entity_id=character.id,
            )

            self.assertEqual((first.version, second.version), (1, 2))
            self.assertFalse(db.get(type(first), first.id).is_selected)
            self.assertTrue(second.is_selected)

            assets, total = list_assets_page(db, project.id, asset_type="image")
            self.assertEqual(total, 2)
            self.assertEqual([asset.id for asset in assets], [second.id, first.id])
            self.assertEqual(
                {asset.id for asset in list_current_assets(db, project.id)},
                {first.id, second.id},
            )

            selected = select_asset(db, first.id)
            self.assertTrue(selected.is_selected)
            self.assertFalse(db.get(type(second), second.id).is_selected)
            first_path = storage_root / first.uri.split("/storage/", 1)[1]
            self.assertTrue(first_path.is_file())

            delete_asset(db, first.id)

            self.assertIsNone(db.get(type(first), first.id))
            self.assertFalse(first_path.exists())
            self.assertTrue(db.get(type(second), second.id).is_selected)

    def test_selecting_and_deleting_video_versions_keeps_shot_duration_in_sync(self):
        with self.session_factory() as db:
            project = Project(title="视频版本生命周期")
            db.add(project)
            db.flush()
            shot = Shot(
                project_id=project.id,
                shot_no=1,
                description="角色挥手",
                duration_sec=Decimal("7"),
                shot_card={
                    "segment_plan": {
                        "duration_mode": "fixed",
                        "planned_duration_sec": "6",
                        "actual_duration_sec": "7",
                    }
                },
            )
            db.add(shot)
            db.flush()
            first = Asset(
                project_id=project.id,
                asset_type="video",
                asset_role="shot_video",
                entity_type="shot",
                entity_id=shot.id,
                version=1,
                uri="https://media.test/shot-v1.mp4",
                duration_sec=Decimal("7"),
                raw_response={"request_metadata": {"duration_request": {"mode": "fixed"}}},
                status="approved",
                is_selected=True,
            )
            second = Asset(
                project_id=project.id,
                asset_type="video",
                asset_role="shot_video",
                entity_type="shot",
                entity_id=shot.id,
                version=2,
                uri="https://media.test/shot-v2.mp4",
                duration_sec=Decimal("9"),
                raw_response={"request_metadata": {"duration_request": {"mode": "fixed"}}},
                status="approved",
                is_selected=False,
            )
            db.add_all([first, second])
            db.commit()

            select_asset(db, second.id)
            db.refresh(shot)
            self.assertEqual(shot.duration_sec, Decimal("9.00"))
            self.assertEqual(shot.shot_card["segment_plan"]["actual_duration_sec"], "9.000")

            delete_asset(db, second.id)
            db.refresh(shot)
            self.assertTrue(db.get(Asset, first.id).is_selected)
            self.assertEqual(shot.duration_sec, Decimal("7.00"))
            self.assertEqual(shot.shot_card["segment_plan"]["actual_duration_sec"], "7.000")

            delete_asset(db, first.id)
            db.refresh(shot)
            self.assertEqual(shot.duration_sec, Decimal("6.00"))
            self.assertNotIn("actual_duration_sec", shot.shot_card["segment_plan"])

    def test_deleting_bound_image_cleans_shot_references(self):
        with self.session_factory() as db:
            project = Project(title="引用清理")
            db.add(project)
            db.flush()
            character = Character(project_id=project.id, name="Mia", status="approved")
            db.add(character)
            db.flush()
            image = Asset(
                project_id=project.id,
                asset_type="image",
                asset_role="character_main_ref",
                entity_type="character",
                entity_id=character.id,
                version=1,
                uri="https://media.test/mia.png",
                status="approved",
                is_selected=True,
            )
            db.add(image)
            db.flush()
            shot = Shot(
                project_id=project.id,
                shot_no=1,
                description="Mia 入场",
                character_ids=[character.id],
                shot_card={
                    "reference_asset_ids": [image.id],
                    "video_reference_asset_id": image.id,
                    "prompt_stale": False,
                },
                status="approved",
            )
            db.add(shot)
            db.commit()

            delete_asset(db, image.id)
            db.refresh(shot)

            self.assertEqual(shot.character_ids, [])
            self.assertEqual(shot.shot_card["reference_asset_ids"], [])
            self.assertNotIn("video_reference_asset_id", shot.shot_card)
            self.assertTrue(shot.shot_card["prompt_stale"])
            self.assertEqual(shot.status, "ready_for_review")


if __name__ == "__main__":
    unittest.main()
