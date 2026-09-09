from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations


class PostgresTaskWriteSafetyMigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "7a8192b3c4d5_add_task_write_safety.py"
        )
        spec = spec_from_file_location("task_write_safety_migration", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Unable to load task write safety migration")
        cls.migration = module_from_spec(spec)
        spec.loader.exec_module(cls.migration)

    def _postgres_sql(self, operation) -> str:
        output = StringIO()
        context = MigrationContext.configure(
            dialect_name="postgresql",
            opts={"as_sql": True, "output_buffer": output},
        )
        with Operations.context(context):
            operation()
        return output.getvalue()

    def test_upgrade_contract_compiles_for_postgres(self):
        sql = self._postgres_sql(self.migration.upgrade)

        self.assertIn("ADD COLUMN lease_token VARCHAR(36)", sql)
        self.assertIn("ADD COLUMN completion_key VARCHAR(160)", sql)
        self.assertIn("uq_assets_completion_key", sql)
        self.assertIn("uq_asset_candidates_completion_key", sql)

    def test_downgrade_contract_compiles_for_postgres(self):
        sql = self._postgres_sql(self.migration.downgrade)

        self.assertIn("DROP CONSTRAINT uq_assets_completion_key", sql)
        self.assertIn("DROP COLUMN lease_token", sql)


if __name__ == "__main__":
    unittest.main()
