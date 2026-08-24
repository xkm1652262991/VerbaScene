from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class ProviderDescriptor(BaseModel):
    name: str
    type: str
    model: str
    capabilities: list[str]
    supports_polling: bool = False
    selected: bool = False
    configuration_status: Literal["ready", "incomplete"] = "ready"
    validation_error: str | None = None
    native_audio: bool = False
    reference_images: bool = False
    reference_videos: bool = False
    reference_audio: bool = False
    multi_reference: bool = False
    smart_duration: bool = False
    min_duration_sec: float | None = None
    max_duration_sec: float | None = None
    supported_resolutions: list[str] = Field(default_factory=list)


ProviderSlot = Literal["llm", "image", "video"]


class ProviderConfigUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_name: str = Field(min_length=1, max_length=120)
    model_name: str = Field(min_length=1, max_length=160)
    base_url: str | None = None
    api_key_ref: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
        description="Environment variable name only; secret values are never persisted here.",
    )
    api_key_mode: Literal["environment", "direct", "none"] | None = Field(
        default=None,
        description="Credential source. Omit for backward-compatible environment-variable behavior.",
    )
    api_key: SecretStr | None = Field(
        default=None,
        description="Write-only API key. It is never returned by provider configuration APIs.",
    )
    default_params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider_name", "model_name", mode="before")
    @classmethod
    def strip_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("base_url", "api_key_ref", mode="before")
    @classmethod
    def empty_text_as_none(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class RuntimeProviderConfigRead(BaseModel):
    id: str | None = None
    provider_type: ProviderSlot
    provider_name: str
    model_name: str
    base_url: str | None = None
    api_key_ref: str | None = None
    api_key_mode: Literal["environment", "direct", "none"] = "none"
    api_key_configured: bool = False
    default_params: dict[str, Any] = Field(default_factory=dict)
    source: Literal["environment", "saved"]
    configuration_status: Literal["ready", "incomplete"]
    validation_error: str | None = None
    restart_required: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)


class ImageProviderProfileRead(BaseModel):
    id: str
    label: str
    provider_name: str
    model_name: str
    base_url: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    default_params: dict[str, Any] = Field(default_factory=dict)
    source: Literal["environment", "saved"]
    is_default: bool = False
    supports_references: bool = False
    configuration_status: Literal["ready", "incomplete"]
    validation_error: str | None = None


class ProviderTestRequest(BaseModel):
    provider_type: str = Field(pattern="^(llm|image|video|vlm)$")
    provider_name: str = "mock"
    project_id: str | None = None
    model: str | None = None
    prompt: str = Field(min_length=1)
    negative_prompt: str | None = None
    references: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderAssetRead(BaseModel):
    asset_type: str
    uri: str
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration_sec: Decimal | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderErrorRead(BaseModel):
    error_code: str
    error_message: str
    is_retryable: bool
    raw_error: dict[str, Any] = Field(default_factory=dict)


class ProviderUsageRead(BaseModel):
    cost: Decimal
    unit: str


class ProviderTestResponse(BaseModel):
    status: str
    provider_task_id: str
    execution_mode: str = "sync"
    poll_after_sec: int | None = None
    task_id: str | None
    assets: list[ProviderAssetRead]
    raw_response: dict[str, Any]
    usage: ProviderUsageRead
    error: ProviderErrorRead | None
    created_at: datetime | None = None
