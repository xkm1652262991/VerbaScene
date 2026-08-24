from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

class ShotFramePromptRead(BaseModel):
    id: str
    project_id: str
    shot_id: str
    frame_type: str
    version: int
    prompt: str
    description: str | None
    layout: str | None
    source: str
    provider: str | None
    model: str | None
    raw_response: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ShotFrameImageRead(BaseModel):
    id: str
    project_id: str
    shot_id: str
    frame_prompt_id: str | None
    asset_id: str | None
    frame_type: str
    image_type: str
    version: int
    provider: str | None
    model: str | None
    prompt: str | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
