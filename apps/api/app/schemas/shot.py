from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ShotRead(BaseModel):
    id: str
    project_id: str
    script_id: str | None
    scene_id: str | None
    shot_no: int
    shot_batch_id: str | None
    is_current: bool
    description: str
    camera_shot: str | None
    camera_movement: str | None
    duration_sec: Decimal | None
    dialogue_ids: list
    character_ids: list
    prop_ids: list
    shot_card: dict[str, Any]
    image_prompt: str | None
    video_prompt: str | None
    negative_prompt: str | None
    generation_mode: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ShotCreate(BaseModel):
    scene_id: str | None = None
    shot_no: int = Field(ge=1)
    description: str = Field(min_length=1)
    camera_shot: str | None = None
    camera_movement: str | None = None
    duration_sec: Decimal | None = None
    dialogue_ids: list = Field(default_factory=list)
    character_ids: list = Field(default_factory=list)
    prop_ids: list = Field(default_factory=list)
    shot_card: dict[str, Any] = Field(default_factory=dict)
    image_prompt: str | None = None
    video_prompt: str | None = None
    negative_prompt: str | None = None
    generation_mode: str = "image_to_video"


class ShotUpdate(BaseModel):
    scene_id: str | None = None
    shot_no: int | None = Field(default=None, ge=1)
    description: str | None = None
    camera_shot: str | None = None
    camera_movement: str | None = None
    duration_sec: Decimal | None = None
    dialogue_ids: list | None = None
    character_ids: list | None = None
    prop_ids: list | None = None
    shot_card: dict[str, Any] | None = None
    image_prompt: str | None = None
    video_prompt: str | None = None
    negative_prompt: str | None = None
    generation_mode: str | None = None
    status: str | None = Field(default=None, pattern="^(draft|ready_for_review|approved|rejected|failed)$")


class ShotVideoReferenceUpdate(BaseModel):
    asset_id: str | None = None


class ShotReferenceAssetsUpdate(BaseModel):
    asset_ids: list[str] = Field(default_factory=list, max_length=24)


class ShotReferenceAssetRead(BaseModel):
    asset_id: str
    token: str
    media_label: str
    entity_type: str
    entity_id: str
    variant_key: str
    reference_role: str
    uri: str


class ShotReorderItem(BaseModel):
    id: str
    shot_no: int = Field(ge=1)


class ShotReorderRequest(BaseModel):
    shots: list[ShotReorderItem]


class ShotGenerationResponse(BaseModel):
    shots: list[ShotRead]
    task_id: str


class ShotPromptPreviewRead(BaseModel):
    shot_id: str
    shot_no: int
    description: str
    image_prompt: str | None
    negative_prompt: str | None
    provider_profile: str
    reference_assets: list[dict[str, Any]]
    compile_notes: list[str]
    blockers: list[str]
    warnings: list[str]
    can_generate: bool


class ShotVideoPromptPreviewRead(BaseModel):
    shot_id: str
    prompt: str
    input_fingerprint: str
    current_fingerprint: str | None = None
    stale: bool
    reference_tokens: list[str] = Field(default_factory=list)
    reference_assets: list[ShotReferenceAssetRead] = Field(default_factory=list)
