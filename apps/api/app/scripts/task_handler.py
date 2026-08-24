from app.db import SessionLocal
from app.platform.tasks.types import TaskLane
from app.services.script_service import SCRIPT_GENERATION_TASK_TYPE, execute_script_generation_task


class ScriptGenerationTaskHandler:
    task_type = SCRIPT_GENERATION_TASK_TYPE
    lane = TaskLane.SCRIPT

    def execute(self, task_id: str) -> None:
        with SessionLocal() as db:
            execute_script_generation_task(db, task_id)

    def cancel(self, task_id: str) -> None:
        # LLM stages are synchronous and do not expose remote cancellation.
        # The pipeline observes cancel_requested_at at its next checkpoint.
        _ = task_id
