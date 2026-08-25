"""Compatibility exports for retired synchronous video use cases.

The active runtime and all implementation ownership live in
``app.production``. New application code must not import this facade.
"""

from app.production.contracts import VIDEO_CANDIDATE_TASK_TYPE
from app.production.legacy_video_generation import (
    create_single_shot_video_candidate_task,
    execute_single_shot_video_candidate_task,
    generate_shot_video_candidates,
    generate_shot_videos,
    generate_single_shot_video,
    generate_single_shot_video_candidate,
    regenerate_video_asset,
    regenerate_video_asset_candidate,
)

__all__ = [
    "VIDEO_CANDIDATE_TASK_TYPE",
    "create_single_shot_video_candidate_task",
    "execute_single_shot_video_candidate_task",
    "generate_shot_video_candidates",
    "generate_shot_videos",
    "generate_single_shot_video",
    "generate_single_shot_video_candidate",
    "regenerate_video_asset",
    "regenerate_video_asset_candidate",
]
