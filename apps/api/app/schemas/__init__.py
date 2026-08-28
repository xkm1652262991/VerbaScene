"""Pydantic schemas."""

from app.schemas.agent import (
    AgentConfigCreate,
    AgentConfigRead,
    AgentConfigUpdate,
    PromptVersionCreate,
    PromptVersionRead,
)
from app.schemas.asset import AssetCandidateGenerationResponse, AssetCandidateRead, AssetRead, ImageGenerationResponse, VideoGenerationResponse
from app.schemas.dialogue import (
    DialogueInput,
    DialogueRead,
    DialogueSaveRequest,
    DialogueSaveResponse,
)
from app.schemas.common import ApiError, ApiResponse, ErrorResponse, PageMeta, PageResponse
from app.schemas.export import CompositionRequest, CompositionResponse, ExportRead
from app.schemas.project import (
    ChapterRead,
    ProjectCreate,
    ProjectDetail,
    ProjectRead,
    ProjectUpdate,
)
from app.schemas.entity import (
    CharacterRead,
    CharacterUpdate,
    EntityBundleRead,
    EntityGenerationResponse,
    PropRead,
    PropUpdate,
    SceneRead,
    SceneUpdate,
)
from app.schemas.provider import (
    ProviderAssetRead,
    ProviderDescriptor,
    ProviderErrorRead,
    ProviderTestRequest,
    ProviderTestResponse,
    ProviderUsageRead,
)
from app.schemas.quality import QualityCheckRead, QualityCheckRunResponse
from app.schemas.script import ScriptRead, ScriptUpdate
from app.schemas.shot import (
    ShotCreate,
    ShotRead,
    ShotReorderItem,
    ShotReorderRequest,
    ShotUpdate,
)
from app.schemas.shot_frame import (
    ShotFrameImageRead,
    ShotFramePromptRead,
)
from app.schemas.task import GenerationTaskProgressRead, GenerationTaskRead, TaskContextLinkRead
from app.schemas.workflow import ProjectStageRunRead

__all__ = [
    "AssetRead",
    "AssetCandidateRead",
    "AssetCandidateGenerationResponse",
    "AgentConfigCreate",
    "AgentConfigRead",
    "AgentConfigUpdate",
    "ChapterRead",
    "CharacterRead",
    "CharacterUpdate",
    "CompositionRequest",
    "CompositionResponse",
    "DialogueInput",
    "DialogueRead",
    "DialogueSaveRequest",
    "DialogueSaveResponse",
    "ApiError",
    "ApiResponse",
    "ErrorResponse",
    "EntityBundleRead",
    "EntityGenerationResponse",
    "ExportRead",
    "GenerationTaskRead",
    "GenerationTaskProgressRead",
    "TaskContextLinkRead",
    "ImageGenerationResponse",
    "PageMeta",
    "PageResponse",
    "ProjectCreate",
    "ProjectDetail",
    "ProjectRead",
    "ProjectStageRunRead",
    "ProjectUpdate",
    "PropRead",
    "PropUpdate",
    "ProviderAssetRead",
    "ProviderDescriptor",
    "ProviderErrorRead",
    "ProviderTestRequest",
    "ProviderTestResponse",
    "ProviderUsageRead",
    "QualityCheckRead",
    "QualityCheckRunResponse",
    "PromptVersionCreate",
    "PromptVersionRead",
    "SceneRead",
    "SceneUpdate",
    "ScriptRead",
    "ScriptUpdate",
    "ShotCreate",
    "ShotRead",
    "ShotReorderItem",
    "ShotReorderRequest",
    "ShotUpdate",
    "ShotFrameImageRead",
    "ShotFramePromptRead",
    "VideoGenerationResponse",
]
