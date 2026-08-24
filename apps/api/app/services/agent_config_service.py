from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AgentConfig, PromptVersion
from app.agents.avd_prompt_pack import AVD_PROMPT_PACK_VERSION, BUILTIN_AVD_PROMPT_TEMPLATES
from app.schemas.agent import AgentConfigCreate, AgentConfigUpdate, PromptVersionCreate, PromptVersionUpdate


@dataclass(frozen=True)
class ResolvedAgentConfig:
    agent_type: str
    provider: str
    model: str
    system_prompt: str | None = None
    temperature: str | None = None
    max_tokens: int | None = None
    settings: dict | None = None


def resolve_agent_config(
    db: Session,
    *,
    agent_type: str,
    default_provider: str,
    default_model: str,
) -> ResolvedAgentConfig:
    config = get_active_agent_config(db, agent_type)
    prompt_version = get_active_prompt_version(db, agent_type)
    return ResolvedAgentConfig(
        agent_type=agent_type,
        provider=config.provider or default_provider if config else default_provider,
        model=config.model or default_model if config else default_model,
        system_prompt=(config.system_prompt if config and config.system_prompt else None)
        or (prompt_version.content if prompt_version else None),
        temperature=config.temperature if config else None,
        max_tokens=config.max_tokens if config else None,
        settings=config.settings if config else {},
    )


def get_active_agent_config(db: Session, agent_type: str) -> AgentConfig | None:
    return db.scalar(
        select(AgentConfig)
        .where(AgentConfig.agent_type == agent_type)
        .where(AgentConfig.is_active.is_(True))
        .order_by(AgentConfig.updated_at.desc()),
    )


def get_active_prompt_version(db: Session, agent_type: str) -> PromptVersion | None:
    return db.scalar(
        select(PromptVersion)
        .where(PromptVersion.agent_type == agent_type)
        .where(PromptVersion.is_active.is_(True))
        .order_by(PromptVersion.version.desc(), PromptVersion.updated_at.desc()),
    )


def list_agent_configs(db: Session, agent_type: str | None = None) -> list[AgentConfig]:
    statement = select(AgentConfig)
    if agent_type:
        statement = statement.where(AgentConfig.agent_type == agent_type)
    return list(db.scalars(statement.order_by(AgentConfig.agent_type, AgentConfig.updated_at.desc())).all())


def list_agent_configs_page(
    db: Session,
    agent_type: str | None = None,
    *,
    offset: int = 0,
    limit: int = 500,
) -> tuple[list[AgentConfig], int]:
    statement = select(AgentConfig)
    count_statement = select(func.count(AgentConfig.id))
    if agent_type:
        statement = statement.where(AgentConfig.agent_type == agent_type)
        count_statement = count_statement.where(AgentConfig.agent_type == agent_type)
    configs = list(
        db.scalars(
            statement.order_by(AgentConfig.agent_type, AgentConfig.updated_at.desc()).offset(offset).limit(limit),
        ).all()
    )
    return configs, db.scalar(count_statement) or 0


def create_agent_config(db: Session, payload: AgentConfigCreate) -> AgentConfig:
    if payload.is_active:
        _deactivate_agent_configs(db, payload.agent_type)
    config = AgentConfig(**payload.model_dump())
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def update_agent_config(db: Session, config_id: str, payload: AgentConfigUpdate) -> AgentConfig:
    config = db.get(AgentConfig, config_id)
    if config is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent config not found")
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("is_active") is True:
        _deactivate_agent_configs(db, config.agent_type)
    for field, value in updates.items():
        setattr(config, field, value)
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def list_prompt_versions(db: Session, agent_type: str | None = None) -> list[PromptVersion]:
    statement = select(PromptVersion)
    if agent_type:
        statement = statement.where(PromptVersion.agent_type == agent_type)
    return list(db.scalars(statement.order_by(PromptVersion.agent_type, PromptVersion.version.desc())).all())


def list_prompt_versions_page(
    db: Session,
    agent_type: str | None = None,
    *,
    offset: int = 0,
    limit: int = 500,
) -> tuple[list[PromptVersion], int]:
    statement = select(PromptVersion)
    count_statement = select(func.count(PromptVersion.id))
    if agent_type:
        statement = statement.where(PromptVersion.agent_type == agent_type)
        count_statement = count_statement.where(PromptVersion.agent_type == agent_type)
    prompts = list(
        db.scalars(
            statement.order_by(PromptVersion.agent_type, PromptVersion.version.desc()).offset(offset).limit(limit),
        ).all()
    )
    return prompts, db.scalar(count_statement) or 0


def create_prompt_version(db: Session, payload: PromptVersionCreate) -> PromptVersion:
    if payload.is_active:
        _deactivate_prompt_versions(db, payload.agent_type)
    data = payload.model_dump()
    metadata = data.pop("metadata", {})
    prompt = PromptVersion(**data, metadata_=metadata)
    db.add(prompt)
    db.commit()
    db.refresh(prompt)
    return prompt


def seed_avd_prompt_pack(db: Session, *, activate: bool = False) -> list[PromptVersion]:
    prompts: list[PromptVersion] = []
    for template in BUILTIN_AVD_PROMPT_TEMPLATES:
        existing = db.scalar(
            select(PromptVersion)
            .where(PromptVersion.agent_type == template.agent_type)
            .where(PromptVersion.source == "avd_builtin")
            .where(PromptVersion.version == AVD_PROMPT_PACK_VERSION)
            .order_by(PromptVersion.updated_at.desc())
        )
        if existing is None:
            existing = PromptVersion(
                agent_type=template.agent_type,
                name=template.name,
                content=template.content,
                version=AVD_PROMPT_PACK_VERSION,
                source="avd_builtin",
                metadata_=template.metadata,
                is_active=False,
            )
        else:
            existing.name = template.name
            existing.content = template.content
            existing.metadata_ = template.metadata
        if activate:
            _deactivate_prompt_versions(db, template.agent_type)
            existing.is_active = True
        db.add(existing)
        prompts.append(existing)
    db.commit()
    for prompt in prompts:
        db.refresh(prompt)
    return prompts


def update_prompt_version(db: Session, prompt_id: str, payload: PromptVersionUpdate) -> PromptVersion:
    prompt = db.get(PromptVersion, prompt_id)
    if prompt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt version not found")
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("is_active") is True:
        _deactivate_prompt_versions(db, prompt.agent_type)
    metadata = updates.pop("metadata", None)
    if metadata is not None:
        prompt.metadata_ = metadata
    for field, value in updates.items():
        setattr(prompt, field, value)
    db.add(prompt)
    db.commit()
    db.refresh(prompt)
    return prompt


def _deactivate_agent_configs(db: Session, agent_type: str) -> None:
    for config in db.scalars(select(AgentConfig).where(AgentConfig.agent_type == agent_type)).all():
        config.is_active = False
        db.add(config)


def _deactivate_prompt_versions(db: Session, agent_type: str) -> None:
    for prompt in db.scalars(select(PromptVersion).where(PromptVersion.agent_type == agent_type)).all():
        prompt.is_active = False
        db.add(prompt)
