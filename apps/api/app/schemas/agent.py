from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentConfigBase(BaseModel):
    agent_type: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    provider: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    temperature: str | None = None
    max_tokens: int | None = None
    max_iterations: int | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class AgentConfigCreate(AgentConfigBase):
    pass


class AgentConfigUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    provider: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    temperature: str | None = None
    max_tokens: int | None = None
    max_iterations: int | None = None
    settings: dict[str, Any] | None = None
    is_active: bool | None = None


class AgentConfigRead(AgentConfigBase):
    id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PromptVersionCreate(BaseModel):
    agent_type: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1)
    version: int = 1
    source: str = "manual"
    metadata: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = False


class PromptVersionUpdate(BaseModel):
    name: str | None = None
    content: str | None = Field(default=None, min_length=1)
    version: int | None = None
    source: str | None = None
    metadata: dict[str, Any] | None = None
    is_active: bool | None = None


class PromptVersionRead(PromptVersionCreate):
    id: str
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_")
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
