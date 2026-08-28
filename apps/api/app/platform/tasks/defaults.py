from threading import Lock

from app.assets.contracts import REFERENCE_IMAGE_BATCH_TASK_TYPE, SHOT_IMAGE_BATCH_TASK_TYPE
from app.assets.frame_extraction_tasks import VideoFrameExtractionTaskHandler
from app.assets.image_batch_coordinator import reconcile_image_batch
from app.assets.image_task_handler import ImageCandidateTaskHandler
from app.exports.task_handler import ProjectExportTaskHandler
from app.platform.tasks.runtime import LocalTaskRuntime, get_task_runtime
from app.production.contracts import PROJECT_VIDEO_BATCH_TASK_TYPE
from app.production.shot_direction.task_handler import ShotDirectionTaskHandler
from app.production.video_task_handler import VideoCandidateTaskHandler
from app.production.video_batch_coordinator import reconcile_project_video_batch
from app.scripts.task_handler import ScriptGenerationTaskHandler


_configured = False
_lock = Lock()


def configure_default_task_runtime() -> LocalTaskRuntime:
    global _configured
    runtime = get_task_runtime()
    with _lock:
        if not _configured:
            runtime.register(ScriptGenerationTaskHandler())
            runtime.register(ShotDirectionTaskHandler())
            runtime.register(ImageCandidateTaskHandler())
            runtime.register(VideoCandidateTaskHandler())
            runtime.register(VideoFrameExtractionTaskHandler())
            runtime.register(ProjectExportTaskHandler())
            runtime.register_parent_reconciler(
                PROJECT_VIDEO_BATCH_TASK_TYPE,
                reconcile_project_video_batch,
            )
            runtime.register_parent_reconciler(
                REFERENCE_IMAGE_BATCH_TASK_TYPE,
                reconcile_image_batch,
            )
            runtime.register_parent_reconciler(
                SHOT_IMAGE_BATCH_TASK_TYPE,
                reconcile_image_batch,
            )
            _configured = True
    return runtime
