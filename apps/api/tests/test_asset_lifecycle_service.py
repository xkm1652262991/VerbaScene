import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.session import create_database_engine, initialize_database
from app.models import Character, Project
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


if __name__ == "__main__":
    unittest.main()
