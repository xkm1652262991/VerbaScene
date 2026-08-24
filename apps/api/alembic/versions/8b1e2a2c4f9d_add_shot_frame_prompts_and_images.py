"""add shot frame prompts and images

Revision ID: 8b1e2a2c4f9d
Revises: 6d2d5df0f7b1
Create Date: 2026-06-08 22:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "8b1e2a2c4f9d"
down_revision: Union[str, None] = "6d2d5df0f7b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "shot_frame_prompts",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("shot_id", sa.String(length=36), nullable=False),
        sa.Column("frame_type", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("layout", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=40), nullable=False, server_default="llm"),
        sa.Column("provider", sa.String(length=120), nullable=True),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column(
            "raw_response",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="ready_for_review"),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shot_id"], ["shots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_shot_frame_prompts_project_id"), "shot_frame_prompts", ["project_id"], unique=False)
    op.create_index(op.f("ix_shot_frame_prompts_shot_id"), "shot_frame_prompts", ["shot_id"], unique=False)
    op.create_index(op.f("ix_shot_frame_prompts_frame_type"), "shot_frame_prompts", ["frame_type"], unique=False)
    op.create_index(op.f("ix_shot_frame_prompts_provider"), "shot_frame_prompts", ["provider"], unique=False)
    op.create_index(op.f("ix_shot_frame_prompts_status"), "shot_frame_prompts", ["status"], unique=False)

    op.create_table(
        "shot_frame_images",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("shot_id", sa.String(length=36), nullable=False),
        sa.Column("frame_prompt_id", sa.String(length=36), nullable=True),
        sa.Column("asset_id", sa.String(length=36), nullable=True),
        sa.Column("frame_type", sa.String(length=40), nullable=False),
        sa.Column("image_type", sa.String(length=40), nullable=False, server_default="generated"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("provider", sa.String(length=120), nullable=True),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="ready_for_review"),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["frame_prompt_id"], ["shot_frame_prompts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shot_id"], ["shots.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_shot_frame_images_project_id"), "shot_frame_images", ["project_id"], unique=False)
    op.create_index(op.f("ix_shot_frame_images_shot_id"), "shot_frame_images", ["shot_id"], unique=False)
    op.create_index(op.f("ix_shot_frame_images_frame_prompt_id"), "shot_frame_images", ["frame_prompt_id"], unique=False)
    op.create_index(op.f("ix_shot_frame_images_asset_id"), "shot_frame_images", ["asset_id"], unique=False)
    op.create_index(op.f("ix_shot_frame_images_frame_type"), "shot_frame_images", ["frame_type"], unique=False)
    op.create_index(op.f("ix_shot_frame_images_provider"), "shot_frame_images", ["provider"], unique=False)
    op.create_index(op.f("ix_shot_frame_images_status"), "shot_frame_images", ["status"], unique=False)

    op.alter_column("shot_frame_prompts", "version", server_default=None)
    op.alter_column("shot_frame_prompts", "source", server_default=None)
    op.alter_column("shot_frame_prompts", "raw_response", server_default=None)
    op.alter_column("shot_frame_prompts", "status", server_default=None)
    op.alter_column("shot_frame_images", "image_type", server_default=None)
    op.alter_column("shot_frame_images", "version", server_default=None)
    op.alter_column("shot_frame_images", "status", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_shot_frame_images_status"), table_name="shot_frame_images")
    op.drop_index(op.f("ix_shot_frame_images_provider"), table_name="shot_frame_images")
    op.drop_index(op.f("ix_shot_frame_images_frame_type"), table_name="shot_frame_images")
    op.drop_index(op.f("ix_shot_frame_images_asset_id"), table_name="shot_frame_images")
    op.drop_index(op.f("ix_shot_frame_images_frame_prompt_id"), table_name="shot_frame_images")
    op.drop_index(op.f("ix_shot_frame_images_shot_id"), table_name="shot_frame_images")
    op.drop_index(op.f("ix_shot_frame_images_project_id"), table_name="shot_frame_images")
    op.drop_table("shot_frame_images")

    op.drop_index(op.f("ix_shot_frame_prompts_status"), table_name="shot_frame_prompts")
    op.drop_index(op.f("ix_shot_frame_prompts_provider"), table_name="shot_frame_prompts")
    op.drop_index(op.f("ix_shot_frame_prompts_frame_type"), table_name="shot_frame_prompts")
    op.drop_index(op.f("ix_shot_frame_prompts_shot_id"), table_name="shot_frame_prompts")
    op.drop_index(op.f("ix_shot_frame_prompts_project_id"), table_name="shot_frame_prompts")
    op.drop_table("shot_frame_prompts")
