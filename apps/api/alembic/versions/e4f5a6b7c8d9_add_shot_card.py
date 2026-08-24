"""add shot card

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-06-10 14:10:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "shots",
        sa.Column(
            "shot_card",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.execute(
        """
        UPDATE shots
        SET shot_card = jsonb_build_object(
            'story_purpose', COALESCE(description, '推进剧情'),
            'emotional_intent', '悬疑、压迫、爽点推进',
            'camera', jsonb_build_object(
                'shot_size', COALESCE(camera_shot, ''),
                'angle', '',
                'movement', COALESCE(camera_movement, '')
            ),
            'action', jsonb_build_object(
                'start_state', COALESCE(description, ''),
                'main_action', COALESCE(video_prompt, description, ''),
                'end_state', COALESCE(description, '')
            ),
            'frame_plan', jsonb_build_object(
                'first_frame', COALESCE(image_prompt, description, ''),
                'key_frame', COALESCE(description, ''),
                'last_frame', COALESCE(video_prompt, description, '')
            ),
            'risk_flags', '[]'::jsonb,
            'review_checklist', '["角色一致", "场景正确", "动作合理", "无水印乱码"]'::jsonb
        )
        WHERE shot_card = '{}'::jsonb
        """
    )
    op.alter_column("shots", "shot_card", server_default=None)


def downgrade() -> None:
    op.drop_column("shots", "shot_card")
