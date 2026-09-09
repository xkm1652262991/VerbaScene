from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, Callable


ProviderProgressCallback = Callable[[dict[str, Any]], None]


class ProviderType(StrEnum):
    LLM = "llm"
    IMAGE = "image"
    VIDEO = "video"
    VLM = "vlm"


class ProviderStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


class ProviderExecutionMode(StrEnum):
    SYNC = "sync"
    ASYNC = "async"
    POLL = "poll"


class SubmissionState(StrEnum):
    NOT_SUBMITTED = "not_submitted"
    ACCEPTED = "accepted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProviderError:
    error_code: str
    error_message: str
    is_retryable: bool = False
    submission_state: SubmissionState = SubmissionState.NOT_SUBMITTED
    raw_error: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderAsset:
    asset_type: str
    uri: str
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    duration_sec: Decimal | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderUsage:
    cost: Decimal = Decimal("0")
    unit: str = "USD"


@dataclass(frozen=True)
class ProviderRequest:
    project_id: str
    task_id: str
    model: str
    prompt: str
    system_prompt: str | None = None
    negative_prompt: str | None = None
    references: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderResponse:
    status: ProviderStatus
    provider_task_id: str
    execution_mode: ProviderExecutionMode = ProviderExecutionMode.SYNC
    poll_after_sec: int | None = None
    assets: list[ProviderAsset] = field(default_factory=list)
    raw_response: dict[str, Any] = field(default_factory=dict)
    usage: ProviderUsage = field(default_factory=ProviderUsage)
    error: ProviderError | None = None
