import importlib.util
from pathlib import Path
import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations


class PostgresJsonbMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "6f708192a3b4_align_workflow_jsonb.py"
        )
        spec = importlib.util.spec_from_file_location("workflow_jsonb_migration", migration_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        cls.migration = module

    def _postgres_sql(self, operation) -> str:
        buffer: list[str] = []
        context = MigrationContext.configure(
            dialect_name="postgresql",
            opts={"as_sql": True, "output_buffer": _ListBuffer(buffer)},
        )
        with Operations.context(context):
            operation()
        return "".join(buffer)

    def test_upgrade_uses_jsonb_and_explicit_casts(self):
        sql = self._postgres_sql(self.migration.upgrade)
        self.assertIn("creative_settings TYPE JSONB USING creative_settings::jsonb", sql)
        self.assertIn("sound_cues TYPE JSONB USING sound_cues::jsonb", sql)

    def test_downgrade_restores_json(self):
        sql = self._postgres_sql(self.migration.downgrade)
        self.assertIn("sound_cues TYPE JSON USING sound_cues::json", sql)
        self.assertIn("creative_settings TYPE JSON USING creative_settings::json", sql)


class _ListBuffer:
    def __init__(self, values: list[str]):
        self.values = values

    def write(self, value: str) -> None:
        self.values.append(value)

    def flush(self) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
