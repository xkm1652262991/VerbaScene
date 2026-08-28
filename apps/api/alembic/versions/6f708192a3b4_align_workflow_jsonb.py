"""align workflow JSON columns with the PostgreSQL JSONB model contract

Revision ID: 6f708192a3b4
Revises: 5e6f708192a3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "6f708192a3b4"
down_revision: str | None = "5e6f708192a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("projects", "creative_settings", server_default=None)
    op.alter_column(
        "projects",
        "creative_settings",
        existing_type=sa.JSON(),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        existing_nullable=False,
        postgresql_using="creative_settings::jsonb",
    )
    op.alter_column(
        "projects",
        "creative_settings",
        server_default=sa.text("'{}'::jsonb"),
    )

    op.alter_column("dialogues", "sound_cues", server_default=None)
    op.alter_column(
        "dialogues",
        "sound_cues",
        existing_type=sa.JSON(),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        existing_nullable=False,
        postgresql_using="sound_cues::jsonb",
    )
    op.alter_column(
        "dialogues",
        "sound_cues",
        server_default=sa.text("'[]'::jsonb"),
    )


def downgrade() -> None:
    op.alter_column("dialogues", "sound_cues", server_default=None)
    op.alter_column(
        "dialogues",
        "sound_cues",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=sa.JSON(),
        existing_nullable=False,
        postgresql_using="sound_cues::json",
    )
    op.alter_column("dialogues", "sound_cues", server_default=sa.text("'[]'::json"))

    op.alter_column("projects", "creative_settings", server_default=None)
    op.alter_column(
        "projects",
        "creative_settings",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=sa.JSON(),
        existing_nullable=False,
        postgresql_using="creative_settings::json",
    )
    op.alter_column("projects", "creative_settings", server_default=sa.text("'{}'::json"))
