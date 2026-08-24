"""default project style to empty

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-06-16 14:15:00.000000
"""

from collections.abc import Sequence

from alembic import op


revision: str = "c8d9e0f1a2b3"
down_revision: str | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE projects SET style = '' WHERE style = 'cyber_suspense'")


def downgrade() -> None:
    op.execute("UPDATE projects SET style = 'cyber_suspense' WHERE style = ''")
