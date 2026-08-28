from app.db import SessionLocal
from app.platform.tasks.types import TaskLane
from app.production.shot_direction.contracts import SHOT_DIRECTION_TASK_TYPE
from app.production.shot_direction.service import cancel_shot_direction_stage, execute_shot_direction_task


class ShotDirectionTaskHandler:
    task_type = SHOT_DIRECTION_TASK_TYPE
    lane = TaskLane.SCRIPT

    def execute(self, task_id: str) -> None:
        with SessionLocal() as db:
            execute_shot_direction_task(db, task_id)

    def cancel(self, task_id: str) -> None:
        # LLM calls do not expose reliable remote cancellation. The running
        # pipeline stops at the next durable checkpoint; this path handles a
        # task claimed after it had already entered `cancelling`.
        with SessionLocal() as db:
            cancel_shot_direction_stage(db, task_id)
