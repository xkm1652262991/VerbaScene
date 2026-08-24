from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ScriptRead(BaseModel):
    id: str
    project_id: str
    chapter_id: str
    version: int
    content: str
    scenes: dict[str, Any] | list[Any]
    dialogues: dict[str, Any] | list[Any]
    status: str
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScriptUpdate(BaseModel):
    scenes: list[dict[str, Any]] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")
