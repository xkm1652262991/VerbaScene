from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AssetStateVariant(BaseModel):
    key: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str
    description: str
    scene_nos: list[int] = Field(default_factory=list)


class AssetSourceEvidence(BaseModel):
    scene_no: int | None = Field(default=None, ge=1)
    evidence: str


class AssetReferencePlan(BaseModel):
    required: bool = True
    priority: Literal["core", "supporting", "optional"] = "supporting"
    views: list[str] = Field(default_factory=list)


class AssetAutofillInfo(BaseModel):
    applied: bool = False
    filled_fields: list[str] = Field(default_factory=list)
    normalized_fields: list[str] = Field(default_factory=list)
    review_fields: list[str] = Field(default_factory=list)


class CharacterAssetSpec(BaseModel):
    schema_version: int = Field(default=0, ge=0)
    autofill: AssetAutofillInfo = Field(default_factory=AssetAutofillInfo)
    aliases: list[str] = Field(default_factory=list)
    entity_kind: str = ""
    species: str = ""
    body_type: str = ""
    facial_features: str = ""
    hair_or_surface: str = ""
    color_palette: list[str] = Field(default_factory=list)
    default_outfit: str = ""
    signature_features: list[str] = Field(default_factory=list)
    default_accessories: list[str] = Field(default_factory=list)
    state_variants: list[AssetStateVariant] = Field(default_factory=list)
    source_evidence: list[AssetSourceEvidence] = Field(default_factory=list)
    reference_plan: AssetReferencePlan = Field(
        default_factory=lambda: AssetReferencePlan(
            required=True,
            priority="core",
            views=["正面全身", "侧面全身", "面部近景"],
        )
    )


class SceneAssetSpec(BaseModel):
    schema_version: int = Field(default=0, ge=0)
    autofill: AssetAutofillInfo = Field(default_factory=AssetAutofillInfo)
    aliases: list[str] = Field(default_factory=list)
    location_type: str = ""
    spatial_layout: str = ""
    fixed_landmarks: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    color_palette: list[str] = Field(default_factory=list)
    zones: list[str] = Field(default_factory=list)
    state_variants: list[AssetStateVariant] = Field(default_factory=list)
    source_evidence: list[AssetSourceEvidence] = Field(default_factory=list)
    reference_plan: AssetReferencePlan = Field(
        default_factory=lambda: AssetReferencePlan(
            required=True,
            priority="supporting",
            views=["空间全景", "关键区域视图"],
        )
    )


class PropAssetSpec(BaseModel):
    schema_version: int = Field(default=0, ge=0)
    autofill: AssetAutofillInfo = Field(default_factory=AssetAutofillInfo)
    aliases: list[str] = Field(default_factory=list)
    shape: str = ""
    dimensions: str = ""
    materials: list[str] = Field(default_factory=list)
    color_palette: list[str] = Field(default_factory=list)
    signature_features: list[str] = Field(default_factory=list)
    scale_reference: str = ""
    holder_relation: str = ""
    story_function: str = ""
    state_variants: list[AssetStateVariant] = Field(default_factory=list)
    source_evidence: list[AssetSourceEvidence] = Field(default_factory=list)
    reference_plan: AssetReferencePlan = Field(default_factory=AssetReferencePlan)


class CharacterRead(BaseModel):
    id: str
    project_id: str
    name: str
    role_type: str | None
    age: str | None
    gender: str | None
    identity: str | None
    personality: str | None
    appearance: str | None
    fixed_prompt: str | None
    asset_spec: CharacterAssetSpec
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CharacterUpdate(BaseModel):
    name: str | None = None
    role_type: str | None = None
    age: str | None = None
    gender: str | None = None
    identity: str | None = None
    personality: str | None = None
    asset_spec: CharacterAssetSpec | None = None
    model_config = ConfigDict(extra="forbid")


class SceneRead(BaseModel):
    id: str
    project_id: str
    name: str
    description: str | None
    visual_style: str | None
    atmosphere: str | None
    fixed_prompt: str | None
    asset_spec: SceneAssetSpec
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SceneUpdate(BaseModel):
    name: str | None = None
    asset_spec: SceneAssetSpec | None = None

    model_config = ConfigDict(extra="forbid")


class PropRead(BaseModel):
    id: str
    project_id: str
    name: str
    description: str | None
    visual_prompt: str | None
    story_function: str | None
    asset_spec: PropAssetSpec
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PropUpdate(BaseModel):
    name: str | None = None
    asset_spec: PropAssetSpec | None = None

    model_config = ConfigDict(extra="forbid")


class EntityBundleRead(BaseModel):
    characters: list[CharacterRead]
    scenes: list[SceneRead]
    props: list[PropRead]


class EntityGenerationResponse(EntityBundleRead):
    task_id: str
