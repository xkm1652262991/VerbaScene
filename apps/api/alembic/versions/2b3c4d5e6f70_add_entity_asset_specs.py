"""add structured entity asset specs

Revision ID: 2b3c4d5e6f70
Revises: 1a2b3c4d5e6f
Create Date: 2026-07-22 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "2b3c4d5e6f70"
down_revision: Union[str, None] = "1a2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _asset_spec_column() -> sa.Column:
    return sa.Column(
        "asset_spec",
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )


def upgrade() -> None:
    for table_name in ("characters", "scenes", "props"):
        op.add_column(table_name, _asset_spec_column())
        op.alter_column(table_name, "asset_spec", server_default=None)


def downgrade() -> None:
    for table_name in ("props", "scenes", "characters"):
        op.drop_column(table_name, "asset_spec")
