from sqlalchemy.orm import Session

from app.models import GenerationTask
from app.scripts.service import create_script_generation_task as _create_task


def create_script_generation_task(
    db: Session,
    project_id: str,
    *,
    idempotency_key: str | None = None,
) -> GenerationTask:
    """Script-module entry point for freezing and creating a runtime task.

    The legacy service export remains temporarily available for maintenance
    commands, while HTTP code depends on the business module boundary.
    """
    return _create_task(
        db,
        project_id,
        idempotency_key=idempotency_key,
    )
