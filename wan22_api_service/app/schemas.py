from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
RunMode = Literal["multi_fsdp", "single_offload"]


class JobCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    size: Literal["1280*720", "720*1280", "832*480", "480*832"] = "1280*720"
    mode: RunMode | None = None
    gpu_devices: str | None = Field(default=None, pattern=r"^[0-9]+(,[0-9]+)*$")
    frame_num: int | None = Field(default=None, ge=1)
    sample_steps: int | None = Field(default=None, ge=1, le=200)
    seed: int | None = Field(default=None, ge=0)


class JobRecord(BaseModel):
    id: str
    status: JobStatus
    prompt: str
    size: str
    mode: RunMode
    gpu_devices: str
    image_path: str
    output_path: str
    log_path: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    return_code: int | None = None
    error: str | None = None
    command: list[str] = []
    frame_num: int | None = None
    sample_steps: int | None = None
    seed: int | None = None


class JobList(BaseModel):
    total: int
    items: list[JobRecord]


class HealthResponse(BaseModel):
    ok: bool
    model_dir_exists: bool
    repo_dir_exists: bool
    queue_depth: int
    current_job_id: str | None
