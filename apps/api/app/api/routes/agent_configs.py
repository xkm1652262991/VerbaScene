from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.common import ApiResponse, PageResponse
from app.schemas.agent import (
    AgentConfigCreate,
    AgentConfigRead,
    AgentConfigUpdate,
    PromptVersionCreate,
    PromptVersionRead,
    PromptVersionUpdate,
)
from app.services.agent_config_service import (
    create_agent_config,
    create_prompt_version,
    list_agent_configs_page,
    list_prompt_versions_page,
    seed_avd_prompt_pack,
    update_agent_config,
    update_prompt_version,
)

router = APIRouter(tags=["agent-configs"])


@router.get("/api/agent-configs", response_model=PageResponse[AgentConfigRead])
def list_agent_configs_endpoint(
    agent_type: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> PageResponse[AgentConfigRead]:
    configs, total = list_agent_configs_page(db, agent_type, offset=offset, limit=limit)
    return PageResponse(items=configs, meta={"total": total, "offset": offset, "limit": limit})


@router.post("/api/agent-configs", response_model=ApiResponse[AgentConfigRead])
def create_agent_config_endpoint(
    payload: AgentConfigCreate,
    db: Session = Depends(get_db),
) -> ApiResponse[AgentConfigRead]:
    return ApiResponse(data=create_agent_config(db, payload))


@router.patch("/api/agent-configs/{config_id}", response_model=ApiResponse[AgentConfigRead])
def update_agent_config_endpoint(
    config_id: str,
    payload: AgentConfigUpdate,
    db: Session = Depends(get_db),
) -> ApiResponse[AgentConfigRead]:
    return ApiResponse(data=update_agent_config(db, config_id, payload))


@router.get("/api/prompt-versions", response_model=PageResponse[PromptVersionRead])
def list_prompt_versions_endpoint(
    agent_type: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> PageResponse[PromptVersionRead]:
    prompts, total = list_prompt_versions_page(db, agent_type, offset=offset, limit=limit)
    return PageResponse(items=prompts, meta={"total": total, "offset": offset, "limit": limit})


@router.post("/api/prompt-versions", response_model=ApiResponse[PromptVersionRead])
def create_prompt_version_endpoint(
    payload: PromptVersionCreate,
    db: Session = Depends(get_db),
) -> ApiResponse[PromptVersionRead]:
    return ApiResponse(data=create_prompt_version(db, payload))


@router.post("/api/prompt-versions/avd/seed", response_model=ApiResponse[list[PromptVersionRead]])
def seed_avd_prompt_pack_endpoint(
    activate: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> ApiResponse[list[PromptVersionRead]]:
    return ApiResponse(data=seed_avd_prompt_pack(db, activate=activate))


@router.patch("/api/prompt-versions/{prompt_id}", response_model=ApiResponse[PromptVersionRead])
def update_prompt_version_endpoint(
    prompt_id: str,
    payload: PromptVersionUpdate,
    db: Session = Depends(get_db),
) -> ApiResponse[PromptVersionRead]:
    return ApiResponse(data=update_prompt_version(db, prompt_id, payload))
