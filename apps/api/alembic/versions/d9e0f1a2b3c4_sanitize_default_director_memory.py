"""sanitize default director memory

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-06-16 14:25:00.000000
"""

from collections.abc import Sequence
import json

from alembic import op
from sqlalchemy import text


revision: str = "d9e0f1a2b3c4"
down_revision: str | None = "c8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DEFAULT_STYLE_MARKERS = (
    "cyberpunk",
    "cyber_suspense",
    "power_fantasy",
    "cold neon",
    "suspense -> pressure",
    "cyberpunk details",
)


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        text("SELECT id, title, target_duration_sec, resolution, aspect_ratio, director_memory FROM projects WHERE style = ''")
    ).mappings()
    for row in rows:
        memory = row["director_memory"] or {}
        serialized = json.dumps(memory, ensure_ascii=False)
        if not any(marker in serialized for marker in DEFAULT_STYLE_MARKERS):
            continue
        target_duration = (
            row["target_duration_sec"]
            or (memory.get("narrative_style") or {}).get("target_duration_sec")
            or 90
        )
        sanitized = {
            "genre_tags": [],
            "visual_style": {
                "palette": "follow source material, no default genre palette",
                "lighting": "cinematic lighting that serves the source material",
                "render_style": "AI comic drama, semi-realistic, horizontal 16:9",
            },
            "narrative_style": {
                "pacing": "hook-first, conflict-driven, compact for short-form video",
                "emotion_curve": "setup -> escalation -> turn -> payoff",
                "dialogue_style": "short, sharp, conflict-driven",
                "target_duration_sec": target_duration,
            },
            "continuity_rules": {
                "character_rules": [
                    "Keep each character's face, hairstyle, outfit silhouette, and signature props stable across shots.",
                ],
                "scene_rules": [
                    "Keep key locations visually consistent in layout, lighting, atmosphere, and source-material details.",
                ],
                "prop_rules": [
                    "Key props must retain shape, color, material, and story function when reused.",
                ],
            },
            "camera_language": {
                "default_aspect_ratio": row["aspect_ratio"] or "16:9",
                "default_resolution": row["resolution"] or "1280x720",
                "preferred_shots": ["close_up", "medium_shot", "wide_establishing_shot"],
                "preferred_motion": ["slow_push_in", "static_tension", "short_lateral_pan"],
            },
            "prompt_constraints": {
                "global_positive": [
                    "cinematic composition",
                    "clear subject",
                    "stable character identity",
                    "no narration",
                ],
                "global_negative": [
                    "watermark",
                    "garbled text",
                    "extra unrelated characters",
                    "opening title",
                    "ending credits",
                    "voice-over narration",
                ],
            },
            "production_notes": [
                f"{row['title']} is produced as a horizontal AI comic drama.",
                "Project style setting: unspecified; follow source material only.",
            ],
            "version": 2,
        }
        connection.execute(
            text("UPDATE projects SET director_memory = CAST(:memory AS jsonb) WHERE id = :id"),
            {"id": row["id"], "memory": json.dumps(sanitized, ensure_ascii=False)},
        )


def downgrade() -> None:
    return None
