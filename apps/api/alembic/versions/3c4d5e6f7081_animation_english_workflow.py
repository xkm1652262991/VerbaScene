"""animation English workflow and native-audio export

Revision ID: 3c4d5e6f7081
Revises: 2b3c4d5e6f70
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "3c4d5e6f7081"
down_revision: str | None = "2b3c4d5e6f70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "creative_settings",
            sa.JSON(),
            nullable=False,
            server_default=sa.text(
                """'{"audience_age":"6-10","english_level":"A1","learning_objective":"","animation_style":"","dialogue_language":"en","translation_language":"zh-CN","default_subtitle_mode":"none"}'"""
            ),
        ),
    )
    op.add_column(
        "chapters",
        sa.Column("input_mode", sa.String(length=40), nullable=False, server_default="imported_script"),
    )
    op.create_index("ix_chapters_input_mode", "chapters", ["input_mode"])

    op.add_column("dialogues", sa.Column("translation_zh", sa.Text(), nullable=True))
    op.add_column(
        "dialogues",
        sa.Column("speaker_name", sa.String(length=120), nullable=False, server_default="Character"),
    )
    op.add_column(
        "dialogues",
        sa.Column("sequence_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("dialogues", sa.Column("beat_id", sa.String(length=80), nullable=True))
    op.add_column(
        "dialogues",
        sa.Column("sound_cues", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.create_index("ix_dialogues_sequence_order", "dialogues", ["sequence_order"])
    op.create_index("ix_dialogues_beat_id", "dialogues", ["beat_id"])

    op.add_column("assets", sa.Column("variant_key", sa.String(length=120), nullable=True))
    op.create_index("ix_assets_variant_key", "assets", ["variant_key"])
    op.add_column("asset_candidates", sa.Column("variant_key", sa.String(length=120), nullable=True))
    op.create_index("ix_asset_candidates_variant_key", "asset_candidates", ["variant_key"])
    op.execute(
        "UPDATE assets SET variant_key = 'base' "
        "WHERE variant_key IS NULL AND entity_type IN ('character', 'scene', 'prop')"
    )
    op.execute(
        "UPDATE asset_candidates SET variant_key = 'base' "
        "WHERE variant_key IS NULL AND entity_type IN ('character', 'scene', 'prop')"
    )

    op.add_column(
        "exports",
        sa.Column("subtitle_mode", sa.String(length=20), nullable=False, server_default="none"),
    )

    op.drop_column("characters", "voice_profile")
    op.drop_column("dialogues", "audio_asset_id")
    op.execute("DELETE FROM provider_configs WHERE provider_type = 'tts'")
    op.execute("UPDATE agent_configs SET is_active = false WHERE agent_type = 'sound_designer'")
    op.execute("UPDATE prompt_versions SET is_active = false WHERE agent_type = 'sound_designer'")
    op.drop_table("project_stages")


def downgrade() -> None:
    op.create_table(
        "project_stages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("stage", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_task_id", sa.String(length=36), nullable=True),
        sa.Column("stage_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "stage", name="uq_project_stages_project_stage"),
    )
    op.create_index("ix_project_stages_project_id", "project_stages", ["project_id"])
    op.create_index("ix_project_stages_stage", "project_stages", ["stage"])
    op.create_index("ix_project_stages_status", "project_stages", ["status"])
    op.create_index("ix_project_stages_last_task_id", "project_stages", ["last_task_id"])

    op.add_column(
        "dialogues",
        sa.Column("audio_asset_id", sa.String(length=36), nullable=True),
    )
    op.create_index("ix_dialogues_audio_asset_id", "dialogues", ["audio_asset_id"])
    op.add_column(
        "characters",
        sa.Column("voice_profile", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )

    op.drop_column("exports", "subtitle_mode")

    op.drop_index("ix_asset_candidates_variant_key", table_name="asset_candidates")
    op.drop_column("asset_candidates", "variant_key")
    op.drop_index("ix_assets_variant_key", table_name="assets")
    op.drop_column("assets", "variant_key")

    op.drop_index("ix_dialogues_beat_id", table_name="dialogues")
    op.drop_index("ix_dialogues_sequence_order", table_name="dialogues")
    op.drop_column("dialogues", "sound_cues")
    op.drop_column("dialogues", "beat_id")
    op.drop_column("dialogues", "sequence_order")
    op.drop_column("dialogues", "translation_zh")
    op.drop_column("dialogues", "speaker_name")

    op.drop_index("ix_chapters_input_mode", table_name="chapters")
    op.drop_column("chapters", "input_mode")
    op.drop_column("projects", "creative_settings")
