from app.db import SessionLocal
from app.platform.tasks.types import TaskLane
from app.scripts.contracts import SCRIPT_GENERATION_TASK_TYPE
from app.scripts.service import execute_script_generation_task


class ScriptGenerationTaskHandler:
    task_type = SCRIPT_GENERATION_TASK_TYPE
    lane = TaskLane.SCRIPT

    def execute(self, task_id: str) -> None:
        with SessionLocal() as db:
            execute_script_generation_task(db, task_id)

    def cancel(self, task_id: str) -> None:
        # OpenAI-compatible LLM streams observe cancellation from their next
        # progress chunk. This path only handles a task claimed after it had
        # already entered `cancelling`.
        _ = task_id
