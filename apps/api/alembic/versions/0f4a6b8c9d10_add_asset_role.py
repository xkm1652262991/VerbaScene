"""add asset role

Revision ID: 0f4a6b8c9d10
Revises: 8b1e2a2c4f9d
Create Date: 2026-06-09 22:10:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0f4a6b8c9d10"
down_revision: Union[str, None] = "8b1e2a2c4f9d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("asset_role", sa.String(length=80), nullable=True))
    op.create_index(op.f("ix_assets_asset_role"), "assets", ["asset_role"], unique=False)

    op.execute(
        """
        UPDATE assets
        SET asset_role = CASE
            WHEN asset_type = 'image' AND entity_type = 'character' THEN 'character_main_ref'
            WHEN asset_type = 'image' AND entity_type = 'scene' THEN 'scene_ref'
            WHEN asset_type = 'image' AND entity_type = 'prop' THEN 'prop_ref'
            WHEN asset_type = 'image' AND entity_type = 'shot' THEN 'shot_storyboard'
            WHEN asset_type = 'image' AND entity_type = 'shot_frame' AND raw_response->>'frame_type' = 'first' THEN 'shot_frame_first'
            WHEN asset_type = 'image' AND entity_type = 'shot_frame' AND raw_response->>'frame_type' = 'key' THEN 'shot_frame_key'
            WHEN asset_type = 'image' AND entity_type = 'shot_frame' AND raw_response->>'frame_type' = 'last' THEN 'shot_frame_last'
            WHEN asset_type = 'image' AND entity_type = 'shot_frame' THEN 'shot_frame_key'
            WHEN asset_type = 'video' AND entity_type = 'shot' THEN 'shot_video'
            WHEN asset_type = 'audio' AND entity_type = 'dialogue' THEN 'dialogue_audio'
            WHEN asset_type = 'subtitle' THEN 'subtitle'
            WHEN asset_type IN ('export', 'final_video') THEN 'final_export'
            ELSE NULL
        END
        """
    )
    op.execute(
        """
        UPDATE assets AS asset
        SET asset_role = CASE
            WHEN frame.frame_type = 'first' THEN 'shot_frame_first'
            WHEN frame.frame_type = 'key' THEN 'shot_frame_key'
            WHEN frame.frame_type = 'last' THEN 'shot_frame_last'
            ELSE 'shot_frame_key'
        END
        FROM shot_frame_images AS frame
        WHERE asset.id = frame.asset_id
        """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_assets_asset_role"), table_name="assets")
    op.drop_column("assets", "asset_role")
