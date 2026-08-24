from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
import unittest

from alembic.migration import MigrationContext
from alembic.operations import Operations


class PostgresTaskRuntimeMigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "4d5e6f708192_add_task_runtime_fields.py"
        )
        spec = spec_from_file_location("task_runtime_migration", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Unable to load task runtime migration")
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

        self.assertIn("ADD COLUMN parent_task_id VARCHAR(36)", sql)
        self.assertIn("ADD COLUMN lease_expires_at TIMESTAMP WITH TIME ZONE", sql)
        self.assertIn("provider_submission_uncertain", sql)
        self.assertIn("ROW_NUMBER() OVER", sql)
        self.assertIn("uq_generation_tasks_active_dedupe_key", sql)
        self.assertIn("fk_generation_tasks_parent_task_id", sql)

    def test_downgrade_contract_compiles_for_postgres(self):
        sql = self._postgres_sql(self.migration.downgrade)

        self.assertIn("DROP CONSTRAINT uq_generation_tasks_idempotency_scope", sql)
        self.assertIn("DROP CONSTRAINT fk_generation_tasks_parent_task_id", sql)
        self.assertIn("DROP COLUMN lease_expires_at", sql)
        self.assertIn("DROP COLUMN parent_task_id", sql)


if __name__ == "__main__":
    unittest.main()
