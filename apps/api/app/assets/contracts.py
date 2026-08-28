"""Task contracts owned by the asset domain."""

IMAGE_CANDIDATE_TASK_TYPE = "image_candidate_generation"
REFERENCE_IMAGE_BATCH_TASK_TYPE = "reference_image_candidate_batch"
SHOT_IMAGE_BATCH_TASK_TYPE = "shot_image_candidate_batch"
VIDEO_FRAME_EXTRACTION_TASK_TYPE = "video_frame_extraction"

IMAGE_BATCH_TASK_TYPES = frozenset(
    {REFERENCE_IMAGE_BATCH_TASK_TYPE, SHOT_IMAGE_BATCH_TASK_TYPE}
)
MANUALLY_RETRYABLE_ASSET_TASK_TYPES = frozenset(
    {IMAGE_CANDIDATE_TASK_TYPE, VIDEO_FRAME_EXTRACTION_TASK_TYPE}
)
