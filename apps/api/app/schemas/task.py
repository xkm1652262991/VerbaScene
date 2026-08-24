from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class TaskContextLinkRead(BaseModel):
    target_type: str
    target_id: str
    role: str
    label: str | None = None


class GenerationTaskProgressRead(BaseModel):
    id: str
    project_id: str
    task_type: str
    provider: str | None
    model: str | None
    status: str
    progress: int
    progress_label: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class GenerationTaskRead(BaseModel):
    id: str
    project_id: str
    task_type: str
    parent_task_id: str | None
    retry_of_task_id: str | None
    resource_key: str | None
    idempotency_key: str | None
    provider: str | None
    model: str | None
    provider_task_id: str | None
    input_payload: dict[str, Any]
    output_asset_ids: list[str]
    result_payload: dict[str, Any]
    status: str
    progress: int
    progress_label: str | None
    retry_count: int
    max_retries: int
    error_code: str | None
    error_message: str | None
    cost_estimate: Decimal | None
    raw_response: dict[str, Any]
    child_summary: dict[str, int]
    context_links: list[TaskContextLinkRead]
    heartbeat_at: datetime | None
    cancel_requested_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
