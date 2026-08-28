from threading import Lock

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
            runtime.register(VideoCandidateTaskHandler())
            runtime.register_parent_reconciler(
                PROJECT_VIDEO_BATCH_TASK_TYPE,
                reconcile_project_video_batch,
            )
            _configured = True
    return runtime
