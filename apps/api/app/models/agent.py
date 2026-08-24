from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSON_DOCUMENT
from app.models.base import IdMixin, TimestampMixin


class AgentConfig(IdMixin, TimestampMixin, Base):
    __tablename__ = "agent_configs"

    agent_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(String(120))
    model: Mapped[str | None] = mapped_column(String(160))
    system_prompt: Mapped[str | None] = mapped_column(Text)
    temperature: Mapped[str | None] = mapped_column(String(32))
    max_tokens: Mapped[int | None] = mapped_column(Integer)
    max_iterations: Mapped[int | None] = mapped_column(Integer)
    settings: Mapped[dict] = mapped_column(JSON_DOCUMENT, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)


class PromptVersion(IdMixin, TimestampMixin, Base):
    __tablename__ = "prompt_versions"

    agent_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source: Mapped[str] = mapped_column(String(80), default="manual", nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON_DOCUMENT, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
