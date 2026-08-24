from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_CREATIVE_SETTINGS: dict[str, Any] = {
    "english_level": "A1",
    "animation_style": "高品质风格化三维儿童动画",
    "dialogue_language": "en",
    "translation_language": "zh-CN",
    "default_subtitle_mode": "none",
}


class CreativeSettings(BaseModel):
    english_level: Literal["A1", "A2"] = "A1"
    animation_style: str = Field(default="高品质风格化三维儿童动画", max_length=500)
    dialogue_language: Literal["en"] = "en"
    translation_language: Literal["zh-CN"] | None = "zh-CN"
    default_subtitle_mode: Literal["none", "en", "bilingual"] = "none"


class ChapterRead(BaseModel):
    id: str
    input_mode: str
    outline: str
    source_text: str
    source_word_count: int
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    input_mode: str = Field(default="ai_brief", pattern="^(ai_brief|imported_script)$")
    outline: str | None = Field(default=None)
    source_text: str | None = Field(default=None)
    style: str = "高品质风格化三维儿童动画"
    target_duration_sec: int | None = Field(default=90, ge=1, le=180)
    aspect_ratio: str = Field(default="16:9", pattern="^(16:9|9:16)$")
    resolution: str = Field(default="854x480", pattern="^(854x480|480x854)$")
    creative_settings: CreativeSettings = Field(default_factory=CreativeSettings)

    @model_validator(mode="after")
    def validate_creation_input(self) -> "ProjectCreate":
        if self.input_mode == "ai_brief" and not (self.outline or "").strip():
            raise ValueError("AI 创意模式需要填写创意描述")
        if self.input_mode == "imported_script" and not (self.source_text or "").strip():
            raise ValueError("导入剧本模式需要填写剧本文本")
        _validate_frame_pair(self.aspect_ratio, self.resolution)
        return self


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    style: str | None = None
    target_duration_sec: int | None = Field(default=None, ge=1, le=1800)
    aspect_ratio: str | None = Field(default=None, pattern="^(16:9|9:16)$")
    resolution: str | None = Field(
        default=None,
        pattern="^(854x480|480x854|1280x720|720x1280)$",
    )
    creative_settings: CreativeSettings | None = None
    status: str | None = None

    @model_validator(mode="after")
    def validate_frame_pair(self) -> "ProjectUpdate":
        if self.aspect_ratio is not None and self.resolution is not None:
            _validate_frame_pair(self.aspect_ratio, self.resolution)
        return self


class ProjectDeleteRequest(BaseModel):
    confirmation_title: str = Field(min_length=1, max_length=255)


class ProjectDeletionPreview(BaseModel):
    project_id: str
    title: str
    record_counts: dict[str, int] = Field(default_factory=dict)
    total_related_records: int = 0
    active_task_count: int = 0
    active_stage_run_count: int = 0
    storage_present: bool = False
    storage_file_count: int = 0
    storage_bytes: int = 0
    can_delete: bool = True
    blocker_reasons: list[str] = Field(default_factory=list)


class ProjectDeleteResult(BaseModel):
    project_id: str
    title: str
    deleted_records: int
    storage_action: str
    storage_trash_path: str | None = None


class ChapterUpsert(BaseModel):
    input_mode: str = Field(default="ai_brief", pattern="^(ai_brief|imported_script)$")
    outline: str = ""
    source_text: str = ""

    @model_validator(mode="after")
    def validate_input(self) -> "ChapterUpsert":
        if self.input_mode == "ai_brief" and not self.outline.strip():
            raise ValueError("AI 创意模式需要填写创意描述")
        if self.input_mode == "imported_script" and not self.source_text.strip():
            raise ValueError("导入剧本模式需要填写剧本文本")
        return self


class PatchImpactRequest(BaseModel):
    target_type: str = Field(min_length=1)
    target_id: str | None = None
    field: str | None = None
    change_summary: str | None = None


class PatchImpactShotRead(BaseModel):
    id: str
    shot_no: int
    scene_id: str | None
    character_ids: list[str] = Field(default_factory=list)
    prop_ids: list[str] = Field(default_factory=list)
    reason: str


class PatchImpactAssetRead(BaseModel):
    id: str
    asset_type: str
    asset_role: str | None
    entity_type: str | None
    entity_id: str | None
    version: int
    is_selected: bool
    status: str
    reason: str


class PatchImpactRead(BaseModel):
    engine: str
    project_id: str
    target: dict[str, Any]
    affected_shots: list[PatchImpactShotRead] = Field(default_factory=list)
    affected_assets: list[PatchImpactAssetRead] = Field(default_factory=list)
    affected_stages: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    requires_human_review: bool


class ProjectRead(BaseModel):
    id: str
    workspace_id: str | None
    user_id: str | None
    title: str
    style: str
    target_duration_sec: int | None
    resolution: str
    aspect_ratio: str
    creative_settings: CreativeSettings
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProjectDetail(ProjectRead):
    chapters: list[ChapterRead] = Field(default_factory=list)


def _validate_frame_pair(aspect_ratio: str, resolution: str) -> None:
    expected = {"16:9": "854x480", "9:16": "480x854"}[aspect_ratio]
    if resolution != expected:
        raise ValueError(f"{aspect_ratio} 必须使用 {expected}")
