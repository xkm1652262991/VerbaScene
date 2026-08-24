from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AssetRead(BaseModel):
    id: str
    project_id: str
    asset_type: str
    asset_role: str | None
    entity_type: str | None
    entity_id: str | None
    variant_key: str | None
    source_task_id: str | None
    source_stage_run_id: str | None
    source_script_id: str | None
    source_shot_batch_id: str | None
    version: int
    uri: str
    mime_type: str | None
    width: int | None
    height: int | None
    duration_sec: Decimal | None
    provider: str | None
    model: str | None
    prompt: str | None
    negative_prompt: str | None
    raw_response: dict[str, Any]
    status: str
    is_selected: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AssetCandidateRead(BaseModel):
    id: str
    project_id: str
    asset_id: str | None
    source_task_id: str | None
    source_stage_run_id: str | None
    source_script_id: str | None
    source_shot_batch_id: str | None
    candidate_type: str
    asset_type: str
    asset_role: str | None
    entity_type: str | None
    entity_id: str | None
    variant_key: str | None
    version: int
    uri: str
    mime_type: str | None
    width: int | None
    height: int | None
    duration_sec: Decimal | None
    provider: str | None
    model: str | None
    prompt: str | None
    negative_prompt: str | None
    raw_response: dict[str, Any]
    status: str
    review_note: str | None
    promoted_asset_id: str | None
    rejected_at: datetime | None
    promoted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AssetCandidateCreate(BaseModel):
    asset_type: str
    uri: str
    asset_role: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    variant_key: str | None = None
    candidate_type: str = "generated"
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration_sec: Decimal | None = None
    provider: str | None = None
    model: str | None = None
    prompt: str | None = None
    negative_prompt: str | None = None
    raw_response: dict[str, Any] = Field(default_factory=dict)
    source_task_id: str | None = None
    source_stage_run_id: str | None = None
    source_script_id: str | None = None
    source_shot_batch_id: str | None = None


class AssetCandidateReviewRequest(BaseModel):
    review_note: str | None = None


class ImageGenerationResponse(BaseModel):
    assets: list[AssetRead]
    task_id: str


class AssetCandidateGenerationResponse(BaseModel):
    candidates: list[AssetCandidateRead]
    task_id: str


class VideoGenerationResponse(BaseModel):
    assets: list[AssetRead]
    task_id: str


class ShotVideoGenerationRequest(BaseModel):
    duration_mode: Literal["provider_auto", "fixed"] | None = None
    duration_sec: Decimal | None = Field(default=None, ge=1, le=30)
    video_prompt: str | None = None

    @model_validator(mode="after")
    def validate_duration_request(self) -> "ShotVideoGenerationRequest":
        if self.duration_mode == "fixed" and self.duration_sec is None:
            raise ValueError("固定时长模式必须提供 duration_sec")
        if self.duration_mode == "provider_auto" and self.duration_sec is not None:
            raise ValueError("智能时长模式不得同时提供 duration_sec")
        return self
