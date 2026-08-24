"""Compatibility facade for asset services.

Business logic belongs to the focused owner modules imported below. New internal
callers must import from those modules directly instead of extending this facade.
"""

from app.services.asset_repository import (
    list_assets,
    list_assets_page,
    list_current_assets,
    get_project_or_404,
    resolve_shot_video_input_asset,
    get_selected_assets,
    get_selected_assets_by_type,
)

from app.services.asset_lifecycle_service import (
    select_asset,
    extract_video_frame_candidate,
    upload_image_asset,
    delete_asset,
)

from app.services.image_generation_service import (
    generate_reference_images,
    generate_reference_image_candidates,
    generate_single_reference_image_candidate,
    generate_shot_images,
    generate_shot_image_candidates,
    generate_single_shot_image_candidate,
    generate_shot_image_grid,
    regenerate_image_asset,
    regenerate_image_asset_candidate,
    regenerate_image_candidate_from_candidate,
)

from app.services.video_generation_service import (
    generate_shot_videos,
    generate_shot_video_candidates,
    generate_single_shot_video,
    generate_single_shot_video_candidate,
    create_single_shot_video_candidate_task,
    execute_single_shot_video_candidate_task,
    regenerate_video_asset,
    regenerate_video_asset_candidate,
)

__all__ = [
    "list_assets",
    "list_assets_page",
    "list_current_assets",
    "get_project_or_404",
    "resolve_shot_video_input_asset",
    "get_selected_assets",
    "get_selected_assets_by_type",
    "select_asset",
    "extract_video_frame_candidate",
    "upload_image_asset",
    "delete_asset",
    "generate_reference_images",
    "generate_reference_image_candidates",
    "generate_single_reference_image_candidate",
    "generate_shot_images",
    "generate_shot_image_candidates",
    "generate_single_shot_image_candidate",
    "generate_shot_image_grid",
    "regenerate_image_asset",
    "regenerate_image_asset_candidate",
    "regenerate_image_candidate_from_candidate",
    "generate_shot_videos",
    "generate_shot_video_candidates",
    "generate_single_shot_video",
    "generate_single_shot_video_candidate",
    "create_single_shot_video_candidate_task",
    "execute_single_shot_video_candidate_task",
    "regenerate_video_asset",
    "regenerate_video_asset_candidate",
]
