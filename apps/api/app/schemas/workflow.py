from pydantic import BaseModel, Field
from datetime import datetime
from typing import Any


class WorkflowStageGateRead(BaseModel):
    stage: str
    label: str
    status: str
    order_index: int
    can_enter: bool
    can_generate: bool
    can_approve: bool
    can_reject: bool
    required_previous_stage: str | None = None
    active_run_id: str | None = None
    reasons: list[str] = Field(default_factory=list)


class WorkflowGateSnapshotRead(BaseModel):
    project_id: str
    stages: list[WorkflowStageGateRead]


class ProjectStageRunRead(BaseModel):
    id: str
    project_id: str
    stage: str
    action: str
    status: str
    task_id: str | None
    input_payload: dict[str, Any]
    output_payload: dict[str, Any]
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    run_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
