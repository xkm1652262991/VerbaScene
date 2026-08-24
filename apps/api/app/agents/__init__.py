"""Agent modules."""

from app.agents.composition import build_export_manifest, total_duration
from app.agents.entity_extraction import build_entity_extraction_prompt, parse_entity_extraction_response
from app.agents.image_generation import (
    character_reference_prompt,
    prop_reference_prompt,
    reference_negative_prompt,
    scene_reference_prompt,
    shot_image_prompt,
    shot_negative_prompt,
)
from app.agents.prompt_engineering import build_shot_video_prompt
from app.agents.project_planner import plan_project
from app.agents.script_contracts import (
    SCRIPT_QUALITY_PIPELINE_VERSION,
    ScriptGenerationInput,
    apply_script_patch,
    augment_script_review,
    build_contract_report,
    fallback_story_blueprint,
    parse_script_patch_response,
    parse_script_review_response,
    parse_story_blueprint_response,
    required_phrases_from_source,
    review_requires_revision,
)
from app.agents.script_prompts import (
    build_script_draft_prompt,
    build_script_patch_prompt,
    build_script_review_prompt,
    build_script_structure_recovery_prompt,
    build_story_blueprint_prompt,
    script_pipeline_system_prompt,
)
from app.agents.script_screenplay import (
    extract_dialogues_from_scenes,
    normalize_production_scenes,
    parse_screenplay_response,
    render_readable_script,
)
from app.agents.shot_breakdown import (
    build_shot_breakdown_prompt,
    parse_shot_breakdown_response,
    segment_planning_contract,
)
from app.agents.video_generation import shot_video_params, shot_video_prompt

__all__ = [
    "build_shot_breakdown_prompt",
    "build_shot_video_prompt",
    "build_export_manifest",
    "build_entity_extraction_prompt",
    "build_script_draft_prompt",
    "build_script_patch_prompt",
    "build_script_review_prompt",
    "build_script_structure_recovery_prompt",
    "build_story_blueprint_prompt",
    "augment_script_review",
    "apply_script_patch",
    "build_contract_report",
    "character_reference_prompt",
    "extract_dialogues_from_scenes",
    "fallback_story_blueprint",
    "plan_project",
    "parse_shot_breakdown_response",
    "parse_screenplay_response",
    "parse_script_review_response",
    "parse_script_patch_response",
    "parse_story_blueprint_response",
    "parse_entity_extraction_response",
    "normalize_production_scenes",
    "prop_reference_prompt",
    "reference_negative_prompt",
    "scene_reference_prompt",
    "render_readable_script",
    "review_requires_revision",
    "required_phrases_from_source",
    "ScriptGenerationInput",
    "script_pipeline_system_prompt",
    "SCRIPT_QUALITY_PIPELINE_VERSION",
    "shot_image_prompt",
    "shot_negative_prompt",
    "shot_video_params",
    "shot_video_prompt",
    "segment_planning_contract",
    "total_duration",
]
