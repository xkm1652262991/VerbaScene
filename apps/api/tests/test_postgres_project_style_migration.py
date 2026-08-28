import importlib.util
from pathlib import Path
import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations


class PostgresProjectStyleMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migration_path = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "5e6f708192a3_expand_project_style.py"
        )
        spec = importlib.util.spec_from_file_location("project_style_migration", migration_path)
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

    def test_upgrade_changes_style_to_text(self):
        sql = self._postgres_sql(self.migration.upgrade)
        self.assertIn("ALTER COLUMN style TYPE TEXT", sql)

    def test_downgrade_restores_varchar_limit(self):
        sql = self._postgres_sql(self.migration.downgrade)
        self.assertIn("ALTER COLUMN style TYPE VARCHAR(80)", sql)


class _ListBuffer:
    def __init__(self, values: list[str]):
        self.values = values

    def write(self, value: str) -> None:
        self.values.append(value)

    def flush(self) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
